"""
MediaFetch Bot — entry point.

Run with: python -m bot.main
"""
import asyncio

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.config import settings
from bot.database import db
from bot.handlers.commands import (
    start_command,
    help_command,
    about_command,
    status_command,
    settings_command,
)
from bot.handlers.media import handle_url_message
from bot.handlers.admin import (
    admin_command,
    admin_callback_router,
    receive_broadcast,
    receive_block_id,
    receive_unblock_id,
    receive_max_size,
    cancel_admin_conversation,
    AWAITING_BROADCAST,
    AWAITING_BLOCK_ID,
    AWAITING_UNBLOCK_ID,
    AWAITING_MAX_SIZE,
)
from bot.handlers.error_handler import global_error_handler
from bot.services.downloader import periodic_cleanup
from bot.utils.logger import logger


async def _periodic_cleanup_task():
    while True:
        try:
            await periodic_cleanup()
        except Exception:  # noqa: BLE001
            logger.exception("Periodic cleanup failed")
        await asyncio.sleep(1800)  # every 30 minutes


async def _on_startup(application: Application):
    await db.init()
    logger.info("Database initialized.")
    application.create_task(_periodic_cleanup_task())
    logger.info("MediaFetch Bot started successfully.")


def build_application() -> Application:
    settings.validate()

    application = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .post_init(_on_startup)
        .build()
    )

    # Basic commands
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("settings", settings_command))

    # Admin conversation (broadcast / block / unblock / max size)
    admin_conversation = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback_router, pattern="^(admin_|toggle_platform_)")],
        states={
            AWAITING_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_broadcast)],
            AWAITING_BLOCK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_block_id)],
            AWAITING_UNBLOCK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_unblock_id)],
            AWAITING_MAX_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_max_size)],
        },
        fallbacks=[CommandHandler("cancel", cancel_admin_conversation)],
        per_message=False,
    )
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(admin_conversation)

    # URL / media message handler (catch-all for plain text messages)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url_message))

    # Global error handler
    application.add_error_handler(global_error_handler)

    return application


def main():
    application = build_application()
    logger.info("Starting MediaFetch Bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
