"""
Handles incoming messages containing a URL: validate -> analyze -> confirm
-> download -> send -> cleanup.
"""
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.database import db
from bot.services import rate_limiter
from bot.services.downloader import download_media, cleanup_path
from bot.services.validator import analyze_url
from bot.utils.logger import logger

PLATFORM_LABELS = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "telegram": "Telegram",
    "direct": "Direct link",
}


async def handle_url_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    text = (message.text or "").strip()

    await db.upsert_user(user.id, user.username, user.first_name)

    if await db.is_blocked(user.id):
        await message.reply_text("🚫 You are blocked from using this bot.")
        return

    if not text.lower().startswith(("http://", "https://")):
        await message.reply_text(
            "Please send a valid public media URL (starting with http:// or https://). "
            "Type /help for more info."
        )
        return

    # Rate limiting
    limited, count = await rate_limiter.is_rate_limited(user.id)
    if limited:
        await message.reply_text(
            "⏳ You're sending requests too quickly. Please wait a bit and try again."
        )
        return
    await rate_limiter.record_request(user.id)

    if rate_limiter.has_active_download(user.id):
        await message.reply_text("⏳ You already have a download in progress. Please wait for it to finish.")
        return

    # Step 1: validate & analyze
    analysis = analyze_url(text)
    if not analysis.is_valid:
        await db.log_download(user.id, text, None, None, status="rejected", error_reason=analysis.reason)
        await message.reply_text(f"❌ {analysis.reason}")
        return

    # Check platform toggles from admin settings
    enabled_platforms = await db.get_enabled_platforms()
    if not enabled_platforms.get(analysis.platform, False):
        await db.log_download(
            user.id, text, analysis.platform, None, status="rejected",
            error_reason="Platform disabled by admin",
        )
        await message.reply_text(
            f"❌ {PLATFORM_LABELS.get(analysis.platform, analysis.platform)} downloads are "
            "currently disabled by the bot admin."
        )
        return

    status_msg = await message.reply_text(
        f"🔍 *Analyzing URL...*\n\n"
        f"Platform: {PLATFORM_LABELS.get(analysis.platform, analysis.platform)}\n"
        f"Status: Validating access...",
        parse_mode="Markdown",
    )

    # Step 2: download
    rate_limiter.mark_download_start(user.id)
    result = None
    try:
        await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        await status_msg.edit_text(
            f"⬇️ *Downloading...*\n\n"
            f"Platform: {PLATFORM_LABELS.get(analysis.platform, analysis.platform)}\n"
            f"Status: Fetching media (this may take a moment)...",
            parse_mode="Markdown",
        )

        result = await download_media(analysis.normalized_url, analysis.platform, user.id)

        if not result.success:
            await db.log_download(
                user.id, text, analysis.platform, None, status="failed", error_reason=result.error
            )
            await status_msg.edit_text(f"❌ *Download failed*\n\n{result.error}", parse_mode="Markdown")
            return

        await status_msg.edit_text(
            f"✅ *Media found!*\n\n"
            f"Platform: {PLATFORM_LABELS.get(analysis.platform, analysis.platform)}\n"
            f"Type: {result.media_type}\n"
            f"Title: {result.title}\n"
            f"Status: Sending to you now...",
            parse_mode="Markdown",
        )

        # Step 3: send back to user
        await _send_media_file(update, context, result)

        await db.log_download(
            user.id, text, analysis.platform, result.media_type,
            status="success", file_size_bytes=result.file_size_bytes,
        )
        await status_msg.delete()

    except Exception as e:  # noqa: BLE001
        logger.exception("Unexpected error handling URL from user %s", user.id)
        await db.log_download(user.id, text, analysis.platform, None, status="failed", error_reason=str(e))
        try:
            await status_msg.edit_text("❌ An unexpected error occurred. Please try again later.")
        except Exception:  # noqa: BLE001
            pass
    finally:
        rate_limiter.mark_download_end(user.id)
        if result is not None:
            cleanup_path(result.file_path)


async def _send_media_file(update: Update, context: ContextTypes.DEFAULT_TYPE, result):
    chat_id = update.message.chat_id
    caption = result.title[:1024] if result.title else None

    with open(result.file_path, "rb") as f:
        if result.media_type == "video":
            await context.bot.send_video(chat_id=chat_id, video=f, caption=caption, supports_streaming=True)
        elif result.media_type == "image":
            await context.bot.send_photo(chat_id=chat_id, photo=f, caption=caption)
        elif result.media_type == "audio":
            await context.bot.send_audio(chat_id=chat_id, audio=f, caption=caption)
        else:
            await context.bot.send_document(chat_id=chat_id, document=f, caption=caption)
