"""Main Telegram bot — long polling, routing, and JSONL event dispatch.

This is the central coordinator:
- Registers Telegram handlers (commands, messages, callbacks)
- Starts the JSONL monitor pool
- Dispatches Claude responses back to the right Telegram topic
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import get_settings
from .handlers.commands import (
    cmd_new,
    cmd_resume,
    cmd_screenshot,
    cmd_start,
    cmd_status,
    cmd_stop,
)
from .handlers.message import handle_text_message
from .handlers.status import TypingManager
from .monitor import MonitorPool
from .parser import EventType, ParsedEvent, format_event_for_telegram
from .session import SessionManager
from .tmux import TmuxManager
from .utils.markdown import to_telegram
from .utils.queue import MessageQueue

logger = logging.getLogger(__name__)


class MetroClaudeBot:
    """The main bot orchestrator."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._session_mgr = SessionManager()
        self._tmux_mgr = TmuxManager()
        self._monitor = MonitorPool()
        self._app: Application | None = None
        self._queue: MessageQueue | None = None
        self._typing: TypingManager | None = None

    async def run(self) -> None:
        """Build and start the bot."""
        logger.info("Starting MetroClaude bot...")

        # Build Telegram application
        self._app = (
            Application.builder()
            .token(self._settings.telegram_bot_token)
            .build()
        )

        # Store shared objects in bot_data
        self._app.bot_data["session_manager"] = self._session_mgr
        self._app.bot_data["tmux_manager"] = self._tmux_mgr
        self._app.bot_data["monitor_pool"] = self._monitor

        # Message queue for rate-limited sending
        self._queue = MessageQueue(self._send_telegram_message)
        self._app.bot_data["message_queue"] = self._queue

        # Typing manager
        self._typing = TypingManager(self._app.bot)
        self._app.bot_data["typing_manager"] = self._typing

        # Register handlers
        self._register_handlers()

        # Set up JSONL monitor callback
        self._monitor.on_events(self._on_claude_events)

        # Ensure tmux session exists
        await self._tmux_mgr.ensure_session()

        # Start monitor
        await self._monitor.start()

        # Start Telegram polling
        logger.info("Bot ready — starting Telegram polling")
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )

        # Keep running until interrupted
        try:
            stop_event = asyncio.Event()
            await stop_event.wait()  # Runs forever
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down MetroClaude...")
        if self._typing:
            self._typing.stop_all()
        await self._monitor.stop()
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        logger.info("MetroClaude stopped.")

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        app = self._app
        if not app:
            return

        # Commands
        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(CommandHandler("new", cmd_new))
        app.add_handler(CommandHandler("stop", cmd_stop))
        app.add_handler(CommandHandler("status", cmd_status))
        app.add_handler(CommandHandler("resume", cmd_resume))
        app.add_handler(CommandHandler("screenshot", cmd_screenshot))

        # Callback queries (inline keyboards)
        app.add_handler(CallbackQueryHandler(self._handle_callback))

        # Text messages (catch-all)
        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text_message,
        ))

        logger.info("Handlers registered")

    # ------------------------------------------------------------------
    # Callback handler (inline keyboards)
    # ------------------------------------------------------------------

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline keyboard button presses."""
        query = update.callback_query
        if not query or not query.data:
            return
        await query.answer()

        data = query.data

        # Resume session
        if data.startswith("resume:"):
            session_id = data.split(":", 1)[1]
            chat_id = update.effective_chat.id
            topic_id = query.message.message_thread_id or 0

            settings = self._settings
            recent = self._session_mgr.recent_sessions()
            match = next((r for r in recent if r.session_id == session_id), None)
            if not match:
                await query.edit_message_text("Session non trouvée.")
                return

            window_name = f"resume-{session_id[:8]}"
            try:
                await self._tmux_mgr.create_window(
                    window_name, match.working_dir, session_id,
                )
                info = self._session_mgr.create(
                    chat_id, topic_id, window_name, match.working_dir,
                )
                info.claude_session_id = session_id
                info.is_running = True

                # Start monitoring
                self._monitor.add_session(session_id, Path(match.working_dir))

                await query.edit_message_text(
                    f"✅ Session reprise : `{session_id[:8]}...`\n"
                    f"📂 `{match.working_dir}`",
                    parse_mode="Markdown",
                )
            except Exception as e:
                await query.edit_message_text(f"❌ Erreur : {e}")

        # Permission responses (Phase 2)
        elif data.startswith("permit:"):
            parts = data.split(":")
            action = parts[1]  # "yes" or "no"
            window_name = parts[2] if len(parts) > 2 else ""
            if window_name:
                if action == "yes":
                    await self._tmux_mgr.send_keys_raw(window_name, "y")
                    await self._tmux_mgr.send_enter(window_name)
                    await query.edit_message_text("✅ Permission accordée")
                else:
                    await self._tmux_mgr.send_keys_raw(window_name, "n")
                    await self._tmux_mgr.send_enter(window_name)
                    await query.edit_message_text("❌ Permission refusée")

    # ------------------------------------------------------------------
    # JSONL event dispatch → Telegram
    # ------------------------------------------------------------------

    def _on_claude_events(self, session_id: str, events: list[ParsedEvent]) -> None:
        """Called by MonitorPool when new events are detected (sync callback).

        We schedule the async dispatch on the running event loop.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._dispatch_events(session_id, events))
        except RuntimeError:
            logger.debug("No running loop, skipping event dispatch")

    async def _dispatch_events(self, session_id: str, events: list[ParsedEvent]) -> None:
        """Process parsed events and send relevant ones to Telegram."""
        # Find which topic this session belongs to
        info = self._session_mgr.find_by_claude_session(session_id)
        if not info:
            return

        chat_id = info.chat_id
        topic_id = info.topic_id if info.topic_id else None

        for event in events:
            formatted = format_event_for_telegram(event)
            if formatted:
                # Start typing when we see tool use, stop on text
                if self._typing:
                    if event.event_type == EventType.TOOL_USE:
                        self._typing.start_typing(chat_id, topic_id)
                    elif event.event_type == EventType.TEXT:
                        self._typing.stop_typing(chat_id, topic_id)

                if self._queue:
                    await self._queue.enqueue(chat_id, formatted, topic_id)

    # ------------------------------------------------------------------
    # Telegram sending
    # ------------------------------------------------------------------

    async def _send_telegram_message(
        self, chat_id: int, text: str, thread_id: int | None,
    ) -> None:
        """Send a message to Telegram with markdown formatting and fallback."""
        if not self._app:
            return

        formatted, parse_mode = to_telegram(text)

        kwargs = {
            "chat_id": chat_id,
            "text": formatted or text,
        }
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        if thread_id:
            kwargs["message_thread_id"] = thread_id

        try:
            await self._app.bot.send_message(**kwargs)
        except Exception:
            # Fallback: send without formatting
            kwargs.pop("parse_mode", None)
            kwargs["text"] = text
            try:
                await self._app.bot.send_message(**kwargs)
            except Exception as e:
                logger.error("Failed to send message: %s", e)
