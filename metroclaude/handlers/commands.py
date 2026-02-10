"""Telegram command handlers — /start, /new, /stop, /status, /resume, /screenshot."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..config import get_settings
from ..hooks import read_session_map
from ..security.auth import check_auth
from ..security.input_sanitizer import sanitize_path_argument

logger = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — welcome message."""
    if not await check_auth(update):
        return
    await update.message.reply_text(
        "*MetroClaude* — Claude Code depuis Telegram\n\n"
        "Commandes :\n"
        "/new — Nouvelle session Claude\n"
        "/stop — Arreter la session du topic\n"
        "/status — Etat des sessions\n"
        "/resume — Reprendre une session recente\n"
        "/screenshot — Capture du terminal\n",
        parse_mode="Markdown",
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /new [project_path] — create new Claude session in this topic."""
    if not await check_auth(update):
        return

    bot_data = context.bot_data
    session_mgr = bot_data.get("session_manager")
    tmux_mgr = bot_data.get("tmux_manager")
    monitor_pool = bot_data.get("monitor_pool")

    if not all([session_mgr, tmux_mgr]):
        await update.message.reply_text("Bot pas encore initialise.")
        return

    chat_id = update.effective_chat.id
    topic_id = update.message.message_thread_id or 0

    # Check if session already exists
    existing = session_mgr.get(chat_id, topic_id)
    if existing and existing.is_running:
        await update.message.reply_text(
            f"Session deja active dans ce topic : `{existing.window_name}`\n"
            "Utilisez /stop d'abord.",
            parse_mode="Markdown",
        )
        return

    # Determine working directory
    settings = get_settings()
    args = context.args
    if args:
        raw_path = sanitize_path_argument(args[0])
        resolved = Path(os.path.expanduser(raw_path)).resolve()
        # P1-SEC4: Validate path is under user's home directory
        if not _is_path_allowed(resolved):
            await update.message.reply_text(
                "Chemin non autorise. Le repertoire doit etre sous votre home."
            )
            logger.warning(
                "Path validation rejected: '%s' (resolved: '%s') from user %d",
                raw_path, resolved, update.effective_user.id,
            )
            return
        if not resolved.is_dir():
            await update.message.reply_text(
                f"Repertoire introuvable : `{resolved}`",
                parse_mode="Markdown",
            )
            return
        work_dir = str(resolved)
    else:
        work_dir = str(settings.working_dir)

    # Create window name from topic name or ID
    topic_name = f"topic-{topic_id}" if topic_id else "general"
    if update.message.reply_to_message and update.message.reply_to_message.forum_topic_created:
        topic_name = update.message.reply_to_message.forum_topic_created.name
    window_name = _sanitize_window_name(topic_name)

    await update.message.reply_text(f"Lancement de Claude dans `{window_name}`...", parse_mode="Markdown")

    try:
        window = await tmux_mgr.create_window(window_name, work_dir)
        # P1-T3: Use actual window name (may have suffix if duplicate)
        actual_name = window.window_name or window_name
        info = session_mgr.create(chat_id, topic_id, actual_name, work_dir)
        info.is_running = True

        await update.message.reply_text(
            f"Session creee !\n"
            f"`{work_dir}`\n"
            f"`tmux attach -t {settings.tmux_session_name}` pour voir",
            parse_mode="Markdown",
        )

        # Wait for SessionStart hook to write session_map.json (pattern from ccbot)
        # This is non-critical — if it fails, the session still works
        tmux_session = settings.tmux_session_name
        map_key = f"{tmux_session}:{actual_name}"
        session_id = await _wait_for_session_map(map_key, max_wait=10.0)
        if session_id and monitor_pool:
            info.claude_session_id = session_id
            try:
                monitor_pool.add_session(session_id)
                logger.info("Monitoring JSONL for session %s", session_id)
            except Exception:
                logger.warning("Could not start JSONL monitoring for %s", session_id, exc_info=True)
        else:
            logger.warning("Could not detect session ID for %s (hook may not have fired)", map_key)
    except Exception:
        # P1-SEC12: Don't expose tracebacks to user
        logger.exception("Failed to create session")
        await update.message.reply_text("Erreur lors de la creation de la session.")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop — stop Claude session in this topic."""
    if not await check_auth(update):
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
        await asyncio.sleep(0.5)
        await tmux_mgr.send_message(info.window_name, "/exit")
        await asyncio.sleep(1)
        await tmux_mgr.kill_window(info.window_name)
    except Exception as e:
        logger.warning("Error stopping session: %s", e)

    if monitor_pool and info.claude_session_id:
        monitor_pool.remove_session(info.claude_session_id)

    session_mgr.remove(chat_id, topic_id)
    await update.message.reply_text("Session arretee.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status — show all active sessions."""
    if not await check_auth(update):
        return

    session_mgr = context.bot_data.get("session_manager")
    if not session_mgr:
        await update.message.reply_text("Bot pas initialise.")
        return

    sessions = session_mgr.all_sessions()
    if not sessions:
        await update.message.reply_text("Aucune session active.")
        return

    lines = ["*Sessions actives :*\n"]
    for s in sessions:
        status = "ON" if s.is_running else "OFF"
        lines.append(f"[{status}] `{s.window_name}` — {Path(s.working_dir).name}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /resume — show recent sessions with inline keyboard."""
    if not await check_auth(update):
        return

    session_mgr = context.bot_data.get("session_manager")
    recent = session_mgr.recent_sessions() if session_mgr else []

    if not recent:
        await update.message.reply_text("Pas de sessions recentes a reprendre.")
        return

    keyboard = []
    for r in recent:
        label = f"{Path(r.working_dir).name} ({r.session_id[:8]})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"resume:{r.session_id}")])

    await update.message.reply_text(
        "Reprendre une session :",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /screenshot — capture terminal content as text."""
    if not await check_auth(update):
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
    except Exception:
        # P1-SEC12: Don't expose tracebacks to user
        logger.exception("Failed to capture terminal")
        await update.message.reply_text("Erreur lors de la capture du terminal.")


# ------------------------------------------------------------------
# Path validation (P1-SEC4)
# ------------------------------------------------------------------

def _is_path_allowed(resolved_path: Path) -> bool:
    """Validate that a resolved path is under the user's home directory.

    Uses resolve() + relative_to() pattern from RichardAtCT.
    Prevents path traversal attacks (e.g., /etc/passwd, /root).
    """
    home = Path.home()
    try:
        resolved_path.relative_to(home)
        return True
    except ValueError:
        return False


async def _wait_for_session_map(map_key: str, max_wait: float = 10.0) -> str | None:
    """Wait for the SessionStart hook to write session_map.json.

    Pattern from ccbot: poll session_map.json until our window key appears.
    The hook is triggered by Claude Code itself when it starts.
    """
    import time
    deadline = time.time() + max_wait
    while time.time() < deadline:
        await asyncio.sleep(1.5)
        session_map = read_session_map()
        if map_key in session_map:
            session_id = session_map[map_key].get("session_id", "")
            if session_id:
                logger.info("Hook detected session %s for %s", session_id, map_key)
                return session_id
    return None


def _sanitize_window_name(name: str) -> str:
    """Sanitize a string for use as tmux window name."""
    # tmux window names: remove dots and special chars
    clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower())
    clean = clean.strip("-")[:30]
    return clean or "session"
