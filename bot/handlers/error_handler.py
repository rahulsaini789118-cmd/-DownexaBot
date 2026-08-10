"""
Global error handler registered on the Application.
"""
from telegram import Update
from telegram.ext import ContextTypes

from bot.utils.logger import logger


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong processing your request. Please try again later."
            )
        except Exception:  # noqa: BLE001
            pass
