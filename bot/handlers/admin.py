"""
Admin panel: /admin command plus inline-keyboard callbacks.

Access control: every handler here checks the caller's user id against
settings.all_admin_ids(). Non-admins get a plain refusal message.
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import settings
from bot.database import db
from bot.utils.logger import logger

# Conversation states for multi-step admin actions
AWAITING_BROADCAST = 1
AWAITING_BLOCK_ID = 2
AWAITING_UNBLOCK_ID = 3
AWAITING_MAX_SIZE = 4


def is_admin(user_id: int) -> bool:
    return user_id in settings.all_admin_ids()


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🚫 Block user", callback_data="admin_block"),
         InlineKeyboardButton("✅ Unblock user", callback_data="admin_unblock")],
        [InlineKeyboardButton("📏 Max file size", callback_data="admin_maxsize")],
        [InlineKeyboardButton("🌐 Platforms", callback_data="admin_platforms")],
        [InlineKeyboardButton("🕒 Recent activity", callback_data="admin_recent")],
    ]
    return InlineKeyboardMarkup(buttons)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 You are not authorized to use this command.")
        return
    await update.message.reply_text(
        "*🛠 Admin Panel*\n\nChoose an option:", parse_mode="Markdown", reply_markup=_main_menu_keyboard()
    )


async def admin_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Not authorized.", show_alert=True)
        return

    await query.answer()
    action = query.data

    if action == "admin_stats":
        await _show_stats(query)
    elif action == "admin_recent":
        await _show_recent(query)
    elif action == "admin_platforms":
        await _show_platform_toggles(query)
    elif action.startswith("toggle_platform_"):
        platform = action.replace("toggle_platform_", "")
        await _toggle_platform(query, platform)
    elif action == "admin_broadcast":
        await query.message.reply_text("✏️ Send the broadcast message now (or /cancel to abort).")
        return AWAITING_BROADCAST
    elif action == "admin_block":
        await query.message.reply_text("✏️ Send the numeric user ID to block (or /cancel to abort).")
        return AWAITING_BLOCK_ID
    elif action == "admin_unblock":
        await query.message.reply_text("✏️ Send the numeric user ID to unblock (or /cancel to abort).")
        return AWAITING_UNBLOCK_ID
    elif action == "admin_maxsize":
        current = await db.get_max_file_size_mb()
        await query.message.reply_text(
            f"Current max file size: {current} MB.\nSend a new value in MB (or /cancel to abort)."
        )
        return AWAITING_MAX_SIZE
    elif action == "admin_menu":
        await query.message.reply_text("*🛠 Admin Panel*", parse_mode="Markdown", reply_markup=_main_menu_keyboard())


async def _show_stats(query):
    total_users = await db.total_users()
    active = await db.active_users()
    total_downloads = await db.total_downloads()
    failed = await db.total_failed()
    text = (
        "*📊 Bot Statistics*\n\n"
        f"👥 Total users: {total_users}\n"
        f"🟢 Active users (7d): {active}\n"
        f"✅ Successful downloads: {total_downloads}\n"
        f"❌ Failed downloads: {failed}\n"
    )
    await query.message.reply_text(text, parse_mode="Markdown", reply_markup=_main_menu_keyboard())


async def _show_recent(query):
    rows = await db.recent_activity(limit=10)
    if not rows:
        text = "No activity yet."
    else:
        lines = ["*🕒 Recent Activity*\n"]
        for r in rows:
            status_emoji = {"success": "✅", "failed": "❌", "rejected": "🚫"}.get(r["status"], "•")
            lines.append(
                f"{status_emoji} user `{r['user_id']}` — {r['platform'] or '?'} — {r['status']}"
            )
        text = "\n".join(lines)
    await query.message.reply_text(text, parse_mode="Markdown", reply_markup=_main_menu_keyboard())


async def _show_platform_toggles(query):
    platforms = await db.get_enabled_platforms()
    buttons = []
    for platform, enabled in platforms.items():
        label = f"{'✅' if enabled else '❌'} {platform.capitalize()}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"toggle_platform_{platform}")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_menu")])
    await query.message.reply_text(
        "*🌐 Supported Platforms*\n\nTap to toggle:", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _toggle_platform(query, platform: str):
    platforms = await db.get_enabled_platforms()
    if platform not in platforms:
        await query.answer("Unknown platform.", show_alert=True)
        return
    new_value = not platforms[platform]
    await db.set_platform_enabled(platform, new_value)
    await _show_platform_toggles(query)


# ---------- Multi-step conversation handlers ----------

async def receive_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    user_ids = await db.all_user_ids()
    sent, failed = 0, 0
    status = await update.message.reply_text(f"📢 Broadcasting to {len(user_ids)} users...")
    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=message_text)
            sent += 1
        except Exception:  # noqa: BLE001
            failed += 1
    await status.edit_text(f"📢 Broadcast complete.\n✅ Sent: {sent}\n❌ Failed: {failed}")
    return ConversationHandler.END


async def receive_block_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text =
