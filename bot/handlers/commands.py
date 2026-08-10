"""
Basic user-facing commands: /start, /help, /about, /status, /settings
"""
import time

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import settings
from bot.database import db

START_TIME = time.time()

WELCOME_TEXT = (
    "👋 *Welcome to MediaFetch Bot!*\n\n"
    "Send a public media URL that you own or have permission to download, "
    "and I'll fetch it for you.\n\n"
    "*Supported sources:*\n"
    "• YouTube\n"
    "• Instagram (public posts)\n"
    "• Telegram public media links\n"
    "• Direct image/video links\n\n"
    "I will *not* download private, login-protected, DRM-protected, or "
    "otherwise restricted content — please only use this for media you're "
    "authorized to access.\n\n"
    "Type /help to see all commands."
)

HELP_TEXT = (
    "*Available commands:*\n\n"
    "/start — Start the bot\n"
    "/help — Show this help message\n"
    "/settings — View your settings\n"
    "/status — Check bot status\n"
    "/about — About this bot\n\n"
    "*How to use:*\n"
    "Just send me a public media URL (YouTube, Instagram, Telegram, or a "
    "direct image/video link) and I'll analyze and fetch it if it's "
    "publicly accessible.\n\n"
    "*Note:* I can't bypass private accounts, logins, paywalls, or DRM. "
    "If content is restricted, you'll get a clear error instead."
)

ABOUT_TEXT = (
    "*MediaFetch Bot*\n\n"
    "A bot that fetches publicly accessible media from supported "
    "platforms, for content you own or have permission to download.\n\n"
    "Built with python-telegram-bot + yt-dlp.\n"
    "This bot does not bypass DRM, logins, or paywalls."
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await db.upsert_user(user.id, user.username, user.first_name)
    await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(ABOUT_TEXT, parse_mode="Markdown")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime_seconds = int(time.time() - START_TIME)
    hours, rem = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    total_downloads = await db.total_downloads()
    max_mb = await db.get_max_file_size_mb()
    platforms = await db.get_enabled_platforms()
    enabled_list = ", ".join(p for p, on in platforms.items() if on) or "none"

    text = (
        "*Bot Status*\n\n"
        f"🟢 Online\n"
        f"⏱ Uptime: {hours}h {minutes}m {seconds}s\n"
        f"📦 Total successful downloads: {total_downloads}\n"
        f"📏 Max file size: {max_mb} MB\n"
        f"🌐 Enabled platforms: {enabled_list}\n"
        f"⏳ Download timeout: {settings.download_timeout_seconds}s\n"
        f"🔁 Rate limit: {settings.rate_limit_max_requests} requests / "
        f"{settings.rate_limit_window_seconds}s"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    max_mb = await db.get_max_file_size_mb()
    text = (
        "*Your Settings*\n\n"
        f"📏 Max file size (bot-wide): {max_mb} MB\n"
        f"🔁 Rate limit: {settings.rate_limit_max_requests} requests per "
        f"{settings.rate_limit_window_seconds}s\n\n"
        "Per-user customization isn't available yet — these limits apply "
        "to everyone and are configured by the bot admin."
    )
    await update.message.reply_text(text, parse_mode="Markdown")
