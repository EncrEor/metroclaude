"""Telegram command handlers — /start, /new, /stop, /status, /resume, /screenshot."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..config import get_settings
from ..security.auth import is_authorized

logger = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — welcome message."""
    if not update.effective_user or not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(
        "🚇 *MetroClaude* — Claude Code depuis Telegram\n\n"
        "Commandes :\n"
        "/new — Nouvelle session Claude\n"
        "/stop — Arrêter la session du topic\n"
        "/status — État des sessions\n"
        "/resume — Reprendre une session récente\n"
        "/screenshot — Capture du terminal\n",
        parse_mode="Markdown",
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /new [project_path] — create new Claude session in this topic."""
    if not update.effective_user or not is_authorized(update.effective_user.id):
        return

    bot_data = context.bot_data
    session_mgr = bot_data.get("session_manager")
    tmux_mgr = bot_data.get("tmux_manager")
    monitor_pool = bot_data.get("monitor_pool")

    if not all([session_mgr, tmux_mgr]):
        await update.message.reply_text("Bot pas encore initialisé.")
        return

    chat_id = update.effective_chat.id
    topic_id = update.message.message_thread_id or 0

    # Check if session already exists
    existing = session_mgr.get(chat_id, topic_id)
    if existing and existing.is_running:
        await update.message.reply_text(
            f"Session déjà active dans ce topic : `{existing.window_name}`\n"
            "Utilisez /stop d'abord.",
            parse_mode="Markdown",
        )
        return

    # Determine working directory
    settings = get_settings()
    args = context.args
    if args:
        work_dir = str(Path(os.path.expanduser(args[0])).resolve())
    else:
        work_dir = str(settings.working_dir)

    # Create window name from topic name or ID
    topic_name = f"topic-{topic_id}" if topic_id else "general"
    if update.message.reply_to_message and update.message.reply_to_message.forum_topic_created:
        topic_name = update.message.reply_to_message.forum_topic_created.name
    window_name = _sanitize_window_name(topic_name)

    await update.message.reply_text(f"🚀 Lancement de Claude dans `{window_name}`...", parse_mode="Markdown")

    try:
        window = await tmux_mgr.create_window(window_name, work_dir)
        info = session_mgr.create(chat_id, topic_id, window_name, work_dir)
        info.is_running = True
        await update.message.reply_text(
            f"✅ Session créée !\n"
            f"📂 `{work_dir}`\n"
            f"🖥 `tmux attach -t {settings.tmux_session_name}` pour voir",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.exception("Failed to create session")
        await update.message.reply_text(f"❌ Erreur : {e}")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop — stop Claude session in this topic."""
    if not update.effective_user or not is_authorized(update.effective_user.id):
        return

    bot_data = context.bot_data
    session_mgr = bot_data.get("session_manager")
    tmux_mgr = bot_data.get("tmux_manager")
    monitor_pool = bot_data.get("monitor_pool")

    chat_id = update.effective_chat.id
    topic_id = update.message.message_thread_id or 0

    info = session_mgr.get(chat_id, topic_id)
    if not info:
        await update.message.reply_text("Pas de session active dans ce topic.")
        return

    # Send Escape then /exit to Claude
    try:
        await tmux_mgr.send_keys_raw(info.window_name, "Escape")
        import asyncio
        await asyncio.sleep(0.5)
        await tmux_mgr.send_message(info.window_name, "/exit")
        await asyncio.sleep(1)
        await tmux_mgr.kill_window(info.window_name)
    except Exception as e:
        logger.warning("Error stopping session: %s", e)

    if monitor_pool and info.claude_session_id:
        monitor_pool.remove_session(info.claude_session_id)

    session_mgr.remove(chat_id, topic_id)
    await update.message.reply_text("🛑 Session arrêtée.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status — show all active sessions."""
    if not update.effective_user or not is_authorized(update.effective_user.id):
        return

    session_mgr = context.bot_data.get("session_manager")
    if not session_mgr:
        await update.message.reply_text("Bot pas initialisé.")
        return

    sessions = session_mgr.all_sessions()
    if not sessions:
        await update.message.reply_text("Aucune session active.")
        return

    lines = ["🚇 *Sessions actives :*\n"]
    for s in sessions:
        status = "🟢" if s.is_running else "🔴"
        lines.append(f"{status} `{s.window_name}` — {Path(s.working_dir).name}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /resume — show recent sessions with inline keyboard."""
    if not update.effective_user or not is_authorized(update.effective_user.id):
        return

    session_mgr = context.bot_data.get("session_manager")
    recent = session_mgr.recent_sessions() if session_mgr else []

    if not recent:
        await update.message.reply_text("Pas de sessions récentes à reprendre.")
        return

    keyboard = []
    for r in recent:
        label = f"{Path(r.working_dir).name} ({r.session_id[:8]})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"resume:{r.session_id}")])

    await update.message.reply_text(
        "🔄 Reprendre une session :",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /screenshot — capture terminal content as text."""
    if not update.effective_user or not is_authorized(update.effective_user.id):
        return

    session_mgr = context.bot_data.get("session_manager")
    tmux_mgr = context.bot_data.get("tmux_manager")

    chat_id = update.effective_chat.id
    topic_id = update.message.message_thread_id or 0

    info = session_mgr.get(chat_id, topic_id) if session_mgr else None
    if not info:
        await update.message.reply_text("Pas de session dans ce topic.")
        return

    try:
        content = await tmux_mgr.capture_pane(info.window_name)
        if content.strip():
            # Send as code block (preserves formatting)
            truncated = content[-3900:]  # Keep last part, leave room for formatting
            await update.message.reply_text(f"```\n{truncated}\n```", parse_mode="Markdown")
        else:
            await update.message.reply_text("Terminal vide.")
    except Exception as e:
        await update.message.reply_text(f"❌ Erreur capture : {e}")


def _sanitize_window_name(name: str) -> str:
    """Sanitize a string for use as tmux window name."""
    # tmux window names: remove dots and special chars
    clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower())
    clean = clean.strip("-")[:30]
    return clean or "session"
