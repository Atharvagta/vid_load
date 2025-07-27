import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
import yt_dlp

# --- Configuration ---
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN environment variable set!")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Helper Functions ---
def filter_video_formats(formats: list) -> list:
    filtered = []
    seen_heights = set()
    allowed_res = {360, 480, 720, 1080}
    for fmt in formats:
        if fmt.get('vcodec') != 'none' and fmt.get('acodec') == 'none' and fmt.get('ext') == 'mp4':
            height = fmt.get('height')
            filesize = fmt.get('filesize') or fmt.get('filesize_approx')
            if height in allowed_res and filesize and height not in seen_heights:
                filtered.append({'format_id': fmt['format_id'], 'height': height, 'filesize': filesize})
                seen_heights.add(height)
    return sorted(filtered, key=lambda x: x['height'], reverse=True)

# --- Bot Handlers (Async) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = (
        "👋 **Welcome!**\n\n"
        "Send me a link to a video from a site like YouTube, Twitter, etc., "
        "and I'll help you download it."
    )
    await update.message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    chat_id = update.message.chat_id
    
    processing_msg = await context.bot.send_message(chat_id=chat_id, text="🔎 Analyzing link...")

    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'skip_download': True}) as ydl:
            info = ydl.extract_info(url, download=False)
        
        context.user_data['video_info'] = info
        video_formats = filter_video_formats(info.get('formats', []))
        
        keyboard = []
        if video_formats:
            for fmt in video_formats:
                size_mb = fmt['filesize'] / (1024 * 1024)
                button_text = f"📹 {fmt['height']}p ({size_mb:.1f} MB)"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=str(fmt['height']))])
        
        keyboard.append([InlineKeyboardButton("🎵 Audio Only (MP3)", callback_data="audio")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        title = info.get('title', 'this video')
        
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=processing_msg.message_id,
            text=f"**{title}**\n\nPlease choose a format to download:",
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Error processing link {url}: {e}")
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=processing_msg.message_id,
            text="❌ Sorry, I couldn't process that link. It might be private, invalid, or from an unsupported site."
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    choice = query.data
    chat_id = query.message.chat_id
    info = context.user_data.get('video_info')

    if not info:
        await query.edit_message_text("Sorry, something went wrong. Please send the link again.")
        return

    url = info.get('webpage_url')
    title = info.get('title', 'video')
    
    await query.edit_message_text(text=f"⬇️ Starting download for **{title}**...\n\nThis may take a moment.", parse_mode=ParseMode.MARKDOWN)

    try:
        output_template = f'{chat_id}_{info.get("id", "video")}.%(ext)s'
        
        if choice == "audio":
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best', 'outtmpl': output_template,
                'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}], 'quiet': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.download([url])
            final_filename = f'{chat_id}_{info.get("id", "video")}.mp3'
            with open(final_filename, 'rb') as audio_file:
                await context.bot.send_audio(chat_id=chat_id, audio=audio_file, title=title)
        else:
            ydl_opts = {
                'format': f'bestvideo[ext=mp4][height={choice}]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': output_template, 'merge_output_format': 'mp4', 'quiet': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.download([url])
            final_filename = f'{chat_id}_{info.get("id", "video")}.mp4'
            with open(final_filename, 'rb') as video_file:
                await context.bot.send_video(chat_id=chat_id, video=video_file, supports_streaming=True)
        
        os.remove(final_filename)
        await query.edit_message_text(text="✅ Download complete!")

    except Exception as e:
        logger.error(f"Failed to download for choice {choice}: {e}")
        await query.edit_message_text(text="❌ An error occurred during download. Please try again.")

def main() -> None:
    """Run the bot."""
    application = Application.builder().token(TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & filters.Entity('url'), handle_link))
    application.add_handler(CallbackQueryHandler(button_handler))

    # Get port and webhook URL from Render environment variables
    PORT = int(os.environ.get('PORT', '8443'))
    # RENDER_EXTERNAL_URL is the an environment variable set by Render.
    WEBHOOK_URL = os.environ.get('RENDER_EXTERNAL_URL')

    if not WEBHOOK_URL:
        logger.error("RENDER_EXTERNAL_URL not set!")
        return
        
    # Run the bot in webhook mode
    # The url_path is a secret path that only you and Telegram should know.
    # We use the bot token as the path, which is a common and secure practice.
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
    )

if __name__ == "__main__":
    main()
