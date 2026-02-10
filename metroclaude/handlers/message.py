"""Text message handler — route Telegram messages to Claude via tmux."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..config import get_settings
from ..security.auth import is_authorized

logger = logging.getLogger(__name__)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle regular text messages — forward to Claude in the right tmux window."""
    if not update.effective_user or not is_authorized(update.effective_user.id):
        return
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if not text:
        return

    settings = get_settings()
    bot_data = context.bot_data
    session_mgr = bot_data.get("session_manager")
    tmux_mgr = bot_data.get("tmux_manager")

    chat_id = update.effective_chat.id
    topic_id = update.message.message_thread_id or 0

    # Find session for this topic
    info = session_mgr.get(chat_id, topic_id) if session_mgr else None
    if not info:
        await update.message.reply_text(
            "Pas de session dans ce topic. Utilisez /new pour en créer une."
        )
        return

    # Check blocked commands
    first_word = text.split()[0] if text.split() else ""
    if first_word in settings.blocked_commands:
        await update.message.reply_text(
            f"⚠️ `{first_word}` est une commande interactive, "
            "non supportée via Telegram.",
            parse_mode="Markdown",
        )
        return

    # Send to tmux
    try:
        info.touch()
        await tmux_mgr.send_message(info.window_name, text)
        logger.info("Sent to '%s': %s", info.window_name, text[:80])
    except Exception as e:
        logger.exception("Failed to send to tmux")
        await update.message.reply_text(f"❌ Erreur envoi : {e}")
