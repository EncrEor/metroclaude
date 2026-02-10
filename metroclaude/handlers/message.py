"""Text message handler — route Telegram messages to Claude via tmux."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..config import get_settings
from ..security.auth import check_auth
from ..security.input_sanitizer import sanitize_tmux_input, DEFAULT_MAX_LENGTH

logger = logging.getLogger(__name__)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle regular text messages — forward to Claude in the right tmux window."""
    bot_data = context.bot_data
    audit = bot_data.get("audit")

    # P1-SEC1: check_auth gives feedback + P1-SEC7: logs failure + P2-SEC6: audit
    if not await check_auth(update, audit=audit):
        return
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if not text:
        return

    user_id = update.effective_user.id

    # P1-SEC3: Limit message length before processing
    if len(text) > DEFAULT_MAX_LENGTH:
        await update.message.reply_text(
            f"Message trop long ({len(text)} chars, max {DEFAULT_MAX_LENGTH})."
        )
        return

    settings = get_settings()
    session_mgr = bot_data.get("session_manager")
    tmux_mgr = bot_data.get("tmux_manager")
    rate_limiter = bot_data.get("rate_limiter")
    typing_mgr = bot_data.get("typing_manager")

    chat_id = update.effective_chat.id
    topic_id = update.message.message_thread_id or 0

    # P1-SEC5: Rate limit per user
    if rate_limiter and not rate_limiter.check_user_rate(user_id):
        if audit:
            await audit.log_rate_limit(
                user_id=user_id,
                count=rate_limiter._max_per_minute,
                limit=rate_limiter._max_per_minute,
            )
        await update.message.reply_text("Trop de messages. Attendez un moment.")
        return

    # Find session for this topic
    info = session_mgr.get(chat_id, topic_id) if session_mgr else None
    if not info:
        await update.message.reply_text(
            "Pas de session dans ce topic. Utilisez /new pour en creer une."
        )
        return

    # Check blocked commands
    first_word = text.split()[0] if text.split() else ""
    if first_word in settings.blocked_commands:
        await update.message.reply_text(
            f"`{first_word}` est une commande interactive, "
            "non supportee via Telegram.",
            parse_mode="Markdown",
        )
        return

    # Sanitize before sending to tmux (P0 security: strip control chars & injection)
    original_len = len(text)
    text = sanitize_tmux_input(text)
    if not text:
        return
    # P2-SEC6: Audit if sanitizer modified the input
    if len(text) != original_len and audit:
        await audit.log_input_sanitized(
            user_id=user_id,
            original_len=original_len,
            sanitized_len=len(text),
        )

    # P1-SEC6: Tmux flood protection
    if rate_limiter and not rate_limiter.check_tmux_flood(info.window_name):
        if audit:
            await audit.log_tmux_flood(
                user_id=user_id,
                window_name=info.window_name,
                cooldown=rate_limiter.remaining_cooldown(info.window_name),
            )
        await update.message.reply_text("Message trop rapide. Attendez 1 seconde.")
        return

    # Send to tmux
    try:
        info.touch()
        await tmux_mgr.send_message(info.window_name, text)
        logger.info("Sent to '%s': %s", info.window_name, text[:80])

        # P1-SEC8: Start typing indicator immediately after send
        if typing_mgr:
            typing_mgr.start_typing(chat_id, topic_id or None)
    except Exception as e:
        # P1-SEC12: Don't expose tracebacks to user
        logger.exception("Failed to send to tmux")
        await update.message.reply_text("Erreur d'envoi au terminal.")


async def handle_forward_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle unrecognized commands — forward Claude commands to tmux (P1-B1).

    Catches commands like /clear, /compact, /cost that should be sent to Claude.
    Blocked commands are rejected with a message.
    """
    audit = context.bot_data.get("audit")
    if not await check_auth(update, audit=audit):
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
    rate_limiter = bot_data.get("rate_limiter")

    chat_id = update.effective_chat.id
    topic_id = update.message.message_thread_id or 0

    # Check blocked commands
    first_word = text.split()[0] if text.split() else ""
    if first_word in settings.blocked_commands:
        await update.message.reply_text(
            f"`{first_word}` est une commande interactive, "
            "non supportee via Telegram.",
            parse_mode="Markdown",
        )
        return

    # P1-SEC5: Rate limit
    if rate_limiter and not rate_limiter.check_user_rate(update.effective_user.id):
        await update.message.reply_text("Trop de messages. Attendez un moment.")
        return

    # Find session
    info = session_mgr.get(chat_id, topic_id) if session_mgr else None
    if not info:
        await update.message.reply_text(
            "Pas de session dans ce topic. Utilisez /new pour en creer une."
        )
        return

    # Sanitize and forward
    text = sanitize_tmux_input(text)
    if not text:
        return

    try:
        await tmux_mgr.send_message(info.window_name, text)
        logger.info("Forwarded command to '%s': %s", info.window_name, text[:80])
    except Exception:
        logger.exception("Failed to forward command to tmux")
        await update.message.reply_text("Erreur d'envoi au terminal.")
