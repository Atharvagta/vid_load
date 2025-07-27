import os
import logging
from flask import Flask, request
from telegram import Bot, Update, ParseMode, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackQueryHandler
import yt_dlp

# --- Configuration ---
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN environment variable set!")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Bot & Flask Setup ---
bot = Bot(token=TOKEN)
app = Flask(__name__)
dispatcher = Dispatcher(bot, None, use_context=True)

# --- Your Helper Functions (Slightly Modified) ---

def filter_video_formats(formats: list) -> list:
    """Filter video formats (MP4) by allowed resolutions."""
    filtered = []
    seen_heights = set()
    allowed_res = {360, 480, 720, 1080} # Common resolutions
    for fmt in formats:
        # We need video-only streams with audio available separately
        if fmt.get('vcodec') != 'none' and fmt.get('acodec') == 'none' and fmt.get('ext') == 'mp4':
            height = fmt.get('height')
            filesize = fmt.get('filesize') or fmt.get('filesize_approx')
            if height in allowed_res and filesize and height not in seen_heights:
                filtered.append({'format_id': fmt['format_id'], 'height': height, 'filesize': filesize})
                seen_heights.add(height)
    return sorted(filtered, key=lambda x: x['height'], reverse=True) # Show best quality first

# --- Bot Command & Message Handlers ---

def start(update: Update, context):
    """Handler for the /start command."""
    welcome_message = (
        "👋 **Welcome!**\n\n"
        "Send me a link to a video from a site like YouTube, Twitter, etc., "
        "and I'll help you download it."
    )
    update.message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)

def handle_link(update: Update, context):
    """Handles incoming video links."""
    url = update.message.text.strip()
    chat_id = update.message.chat_id
    
    # Let user know we are processing the link
    processing_msg = context.bot.send_message(chat_id=chat_id, text="🔎 Analyzing link...")

    try:
        # Use yt-dlp to extract info without downloading
        with yt_dlp.YoutubeDL({'quiet': True, 'skip_download': True}) as ydl:
            info = ydl.extract_info(url, download=False)
        
        # Store info for later use when user clicks a button
        context.user_data['video_info'] = info
        video_formats = filter_video_formats(info.get('formats', []))
        
        keyboard = []
        # Create a button for each available video resolution
        if video_formats:
            for fmt in video_formats:
                size_mb = fmt['filesize'] / (1024 * 1024)
                button_text = f"📹 {fmt['height']}p ({size_mb:.1f} MB)"
                # Callback data will be the height, e.g., "720"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=str(fmt['height']))])
        
        # Add an "Audio Only" button
        keyboard.append([InlineKeyboardButton("🎵 Audio Only (MP3)", callback_data="audio")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        title = info.get('title', 'this video')
        
        # Edit the "processing" message to show the options
        context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=processing_msg.message_id,
            text=f"**{title}**\n\nPlease choose a format to download:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Error processing link {url}: {e}")
        context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=processing_msg.message_id,
            text="❌ Sorry, I couldn't process that link. It might be private, invalid, or from an unsupported site."
        )

def button_handler(update: Update, context):
    """Handles button presses for format selection."""
    query = update.callback_query
    query.answer() # Acknowledge the button press

    choice = query.data
    chat_id = query.message.chat_id
    info = context.user_data.get('video_info')

    if not info:
        query.edit_message_text("Sorry, something went wrong. Please send the link again.")
        return

    url = info.get('webpage_url')
    title = info.get('title', 'video')
    
    # Let user know the download is starting
    query.edit_message_text(text=f"⬇️ Starting download for **{title}**...\n\nThis may take a moment.", parse_mode=ParseMode.MARKDOWN)

    try:
        # Define a unique filename for the download
        output_template = f'{chat_id}_{info.get("id", "video")}.%(ext)s'
        
        if choice == "audio":
            # --- Audio Download Logic ---
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'outtmpl': output_template,
                'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}],
                'quiet': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            
            # The final filename will have .mp3
            final_filename = f'{chat_id}_{info.get("id", "video")}.mp3'
            context.bot.send_audio(chat_id=chat_id, audio=open(final_filename, 'rb'), title=title)
            os.remove(final_filename)
        else:
            # --- Video Download Logic ---
            ydl_opts = {
                'format': f'bestvideo[ext=mp4][height={choice}]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': output_template,
                'merge_output_format': 'mp4',
                'quiet': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # The final filename will have .mp4
            final_filename = f'{chat_id}_{info.get("id", "video")}.mp4'
            context.bot.send_video(chat_id=chat_id, video=open(final_filename, 'rb'), supports_streaming=True)
            os.remove(final_filename)

        # Let user know it's done
        query.edit_message_text(text="✅ Download complete!")

    except Exception as e:
        logger.error(f"Failed to download for choice {choice}: {e}")
        query.edit_message_text(text="❌ An error occurred during download. Please try again.")

# --- Flask Web Routes ---

@app.route('/webhook', methods=['POST'])
def webhook():
    """Listens for updates from Telegram."""
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return 'ok'

@app.route('/')
def index():
    """Confirms the web server is running."""
    return 'Bot is alive!'

# --- Register handlers ---
dispatcher.add_handler(CommandHandler("start", start))
# Handle any text message that looks like a URL
dispatcher.add_handler(MessageHandler(Filters.text & Filters.entity('url'), handle_link))
dispatcher.add_handler(CallbackQueryHandler(button_handler))