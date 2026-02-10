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

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import get_settings
from .hooks import cleanup_stale_map_entries, register_hook
from .handlers.commands import (
    cmd_new,
    cmd_resume,
    cmd_screenshot,
    cmd_start,
    cmd_status,
    cmd_stop,
)
from .handlers.interactive import (
    InteractiveTracker,
    build_keyboard_for_ui,
    build_restart_keyboard,
    format_exit_text,
    format_permission_text,
    format_askuser_text,
)
from .handlers.callback_data import (
    CB_ASKUSER,
    CB_RESTART, CB_REFRESH,
    PREFIX_TO_TMUX_KEY, decode_callback,
)
from .handlers.message import handle_text_message, handle_forward_command
from .handlers.status import TypingManager, detect_interactive_ui, detect_claude_exit
from .monitor import MonitorPool
from .parser import EventType, ParsedEvent, format_event_for_telegram
from .session import SessionManager
from .security.audit import AuditLogger
from .security.rate_limiter import RateLimiter
from .tmux import TmuxManager
from .utils.markdown import to_telegram
from .utils.queue import MessageQueue, MessageTask, TaskType

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
        self._rate_limiter = RateLimiter()
        self._audit = AuditLogger()
        self._interactive_tracker: InteractiveTracker | None = None
        self._poll_task: asyncio.Task | None = None
        # P1-M1: Pending tool_use events keyed by tool_id → formatted display text
        self._pending_tools: dict[str, str] = {}

    async def run(self) -> None:
        """Build and start the bot."""
        logger.info("Starting MetroClaude bot...")

        # Register Claude Code SessionStart hook (pattern from ccbot)
        register_hook()

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
        self._app.bot_data["rate_limiter"] = self._rate_limiter
        self._app.bot_data["audit"] = self._audit

        # Message queue for rate-limited sending (send + edit + delete)
        self._queue = MessageQueue(
            self._send_telegram_message,
            self._edit_telegram_message,
            self._delete_telegram_message,
        )
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

        # Interactive tracker (dedup keyboards sent to Telegram)
        self._interactive_tracker = InteractiveTracker()
        self._app.bot_data["interactive_tracker"] = self._interactive_tracker

        # Start status polling (detect permissions, exits)
        self._poll_task = asyncio.create_task(self._status_poll_loop())

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
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
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

        # Text messages (non-commands)
        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text_message,
        ))

        # P1-B1: Forward unrecognized commands to tmux (/clear, /compact, etc.)
        app.add_handler(MessageHandler(
            filters.COMMAND,
            handle_forward_command,
        ))

        # P1-B2: Topic closed handler — cleanup session when topic is closed
        app.add_handler(MessageHandler(
            filters.StatusUpdate.FORUM_TOPIC_CLOSED,
            self._handle_topic_closed,
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

        # Legacy resume handler (backward compat)
        if data.startswith("resume:"):
            await self._handle_resume_callback(query)
            return

        # Legacy permit handler (backward compat)
        if data.startswith("permit:"):
            await self._handle_legacy_permit_callback(query)
            return

        # New unified callback system
        prefix, window_name, index = decode_callback(data)

        if not window_name:
            await query.edit_message_text("Donnees invalides.")
            return

        # Simple yes/no handlers (permission, planmode, restore)
        if prefix in PREFIX_TO_TMUX_KEY:
            key = PREFIX_TO_TMUX_KEY[prefix]
            try:
                await self._tmux_mgr.send_keys_raw(window_name, key)
                await self._tmux_mgr.send_enter(window_name)
                # Clear tracker and edit message
                if self._interactive_tracker:
                    self._interactive_tracker.clear(window_name)
                label = "Approuve" if key == "y" else "Refuse"
                await query.edit_message_text(label)
            except Exception as e:
                logger.warning("Callback send_keys error: %s", e)
                await query.edit_message_text("Erreur d'envoi.")
            return

        # AskUserQuestion — send option number
        if prefix == CB_ASKUSER:
            if index is not None:
                try:
                    await self._tmux_mgr.send_keys_raw(window_name, str(index + 1))
                    await self._tmux_mgr.send_enter(window_name)
                    if self._interactive_tracker:
                        self._interactive_tracker.clear(window_name)
                    await query.edit_message_text(f"Option {index + 1} selectionnee")
                except Exception as e:
                    logger.warning("AskUser callback error: %s", e)
                    await query.edit_message_text("Erreur d'envoi.")
            return

        # Restart Claude
        if prefix == CB_RESTART:
            try:
                info = self._session_mgr.find_by_window(window_name)
                if info and info.claude_session_id:
                    cmd = f"{self._settings.claude_command} --resume {info.claude_session_id}"
                else:
                    cmd = self._settings.claude_command
                # Send command to the pane (shell should be active since Claude exited)
                await self._tmux_mgr.send_message(window_name, cmd)
                if self._interactive_tracker:
                    self._interactive_tracker.clear(window_name)
                if info:
                    info.is_running = True
                await query.edit_message_text("Claude relance")
            except Exception as e:
                logger.warning("Restart callback error: %s", e)
                await query.edit_message_text("Erreur lors du redemarrage.")
            return

        # Refresh — re-capture terminal
        if prefix == CB_REFRESH:
            try:
                terminal = await self._tmux_mgr.capture_pane(window_name)
                truncated = terminal[-3900:]
                await query.edit_message_text(f"```\n{truncated}\n```", parse_mode="Markdown")
                if self._interactive_tracker:
                    self._interactive_tracker.clear(window_name)
            except Exception as e:
                logger.warning("Refresh callback error: %s", e)
                await query.edit_message_text("Erreur de capture.")
            return

    async def _handle_resume_callback(self, query) -> None:
        """Handle legacy resume: callback data."""
        data = query.data
        session_id = data.split(":", 1)[1]
        chat_id = query.message.chat.id
        topic_id = query.message.message_thread_id or 0

        recent = self._session_mgr.recent_sessions()
        match = next((r for r in recent if r.session_id == session_id), None)
        if not match:
            await query.edit_message_text("Session non trouvee.")
            return

        window_name = f"resume-{session_id[:8]}"
        try:
            window = await self._tmux_mgr.create_window(
                window_name, match.working_dir, session_id,
            )
            # P1-T3: Use actual window name (may have suffix)
            actual_name = window.window_name or window_name
            info = self._session_mgr.create(
                chat_id, topic_id, actual_name, match.working_dir,
            )
            info.claude_session_id = session_id
            info.is_running = True

            # Start monitoring
            self._monitor.add_session(session_id, Path(match.working_dir))

            await query.edit_message_text(
                f"Session reprise : `{session_id[:8]}...`\n"
                f"`{match.working_dir}`",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("Resume callback error: %s", e)
            await query.edit_message_text("Erreur lors de la reprise.")

    async def _handle_legacy_permit_callback(self, query) -> None:
        """Handle legacy permit: callback data (backward compat)."""
        data = query.data
        parts = data.split(":")
        action = parts[1]  # "yes" or "no"
        window_name = parts[2] if len(parts) > 2 else ""
        if window_name:
            if action == "yes":
                await self._tmux_mgr.send_keys_raw(window_name, "y")
                await self._tmux_mgr.send_enter(window_name)
                await query.edit_message_text("Permission accordee")
            else:
                await self._tmux_mgr.send_keys_raw(window_name, "n")
                await self._tmux_mgr.send_enter(window_name)
                await query.edit_message_text("Permission refusee")

    # ------------------------------------------------------------------
    # P1-B2: Topic closed handler
    # ------------------------------------------------------------------

    async def _handle_topic_closed(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle forum topic closed — cleanup session and kill tmux window."""
        if not update.message:
            return

        chat_id = update.effective_chat.id
        topic_id = update.message.message_thread_id or 0

        info = self._session_mgr.get(chat_id, topic_id)
        if not info:
            return

        logger.info(
            "Topic %d closed in chat %d, cleaning up window '%s'",
            topic_id, chat_id, info.window_name,
        )

        # Kill tmux window
        try:
            await self._tmux_mgr.kill_window(info.window_name)
        except Exception as e:
            logger.warning("Error killing window on topic close: %s", e)

        # Stop monitoring
        if info.claude_session_id:
            self._monitor.remove_session(info.claude_session_id)

        # Remove session
        self._session_mgr.remove(chat_id, topic_id)

        # Stop typing
        if self._typing:
            self._typing.stop_typing(chat_id, topic_id or None)

    # ------------------------------------------------------------------
    # Status polling — detect interactive UI and Claude exit
    # ------------------------------------------------------------------

    async def _status_poll_loop(self) -> None:
        """Background task: poll terminal state for all active sessions.

        Every ~2s, capture each session's terminal and check for:
        - Interactive UI (permission, AskUser, PlanMode) -> send keyboard
        - Claude exit -> send restart button
        - P1-S3+SEC10: Stale session cleanup (every ~30s)
        """
        stale_check_counter = 0
        while True:
            try:
                await asyncio.sleep(2.0)
                if not self._session_mgr or not self._interactive_tracker:
                    continue

                # P1-S3+SEC10+S5: Periodic stale session cleanup (~30s)
                stale_check_counter += 1
                if stale_check_counter >= 15:
                    stale_check_counter = 0
                    try:
                        windows = await self._tmux_mgr.list_windows()
                        live_names = {w.window_name for w in windows}
                        stale = self._session_mgr.cleanup_stale_sessions(live_names)
                        # P1-S5: Also clean session_map.json
                        if stale:
                            cleanup_stale_map_entries(live_names)
                        for info in stale:
                            if info.claude_session_id:
                                self._monitor.remove_session(info.claude_session_id)
                            if self._typing:
                                self._typing.stop_typing(
                                    info.chat_id,
                                    info.topic_id if info.topic_id else None,
                                )
                    except Exception:
                        logger.debug("Stale cleanup error", exc_info=True)

                sessions = self._session_mgr.all_sessions()
                for info in sessions:
                    if not info.is_running:
                        continue
                    try:
                        await self._poll_session(info)
                    except Exception:
                        logger.debug("Poll error for %s", info.window_name, exc_info=True)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Status poll loop error")
                await asyncio.sleep(5)  # Back off on error

    async def _poll_session(self, info) -> None:
        """Poll a single session for interactive UI or exit."""
        # Check for Claude exit first
        cmd = await self._tmux_mgr.get_pane_current_command(info.window_name)
        if detect_claude_exit(cmd):
            if self._interactive_tracker.should_send(info.window_name, "exit", "exit"):
                keyboard = build_restart_keyboard(info.window_name)
                text = format_exit_text(info.window_name)
                topic_id = info.topic_id if info.topic_id else None
                try:
                    msg = await self._app.bot.send_message(
                        chat_id=info.chat_id,
                        text=text,
                        reply_markup=keyboard,
                        message_thread_id=topic_id,
                    )
                    self._interactive_tracker.mark_sent(info.window_name, "exit", msg.message_id, "exit")
                except Exception as e:
                    logger.debug("Failed to send exit notification: %s", e)
                # Stop typing
                if self._typing:
                    self._typing.stop_typing(info.chat_id, topic_id)
            return  # Don't check for interactive UI if Claude exited

        # Check for interactive UI
        terminal = await self._tmux_mgr.capture_pane(info.window_name)
        ui_info = detect_interactive_ui(terminal)
        if ui_info:
            content_hash = ui_info.content[:100]  # First 100 chars as dedup key
            if self._interactive_tracker.should_send(info.window_name, ui_info.name, content_hash):
                keyboard = build_keyboard_for_ui(ui_info, info.window_name)
                if keyboard:
                    # Format text based on UI type
                    if ui_info.name == "PermissionPrompt":
                        text = format_permission_text(ui_info.content)
                    elif ui_info.name == "AskUserQuestion":
                        text = format_askuser_text(ui_info.content)
                    else:
                        text = f"{ui_info.name}\n\n```\n{ui_info.content[:500]}\n```"

                    topic_id = info.topic_id if info.topic_id else None
                    try:
                        msg = await self._app.bot.send_message(
                            chat_id=info.chat_id,
                            text=text,
                            reply_markup=keyboard,
                            message_thread_id=topic_id,
                            parse_mode="Markdown",
                        )
                        self._interactive_tracker.mark_sent(
                            info.window_name, ui_info.name, msg.message_id, content_hash,
                        )
                    except Exception as e:
                        logger.debug("Failed to send interactive keyboard: %s", e)

                # Stop typing while waiting for user input
                if self._typing:
                    topic_id = info.topic_id if info.topic_id else None
                    self._typing.stop_typing(info.chat_id, topic_id)

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
            logger.debug("No session info for %s, skipping dispatch", session_id)
            return

        chat_id = info.chat_id
        topic_id = info.topic_id if info.topic_id else None
        logger.info("Dispatching %d event(s) for session %s → chat %d topic %s",
                     len(events), session_id[:8], chat_id, topic_id)

        for event in events:
            # P1-SEC8: Typing management connected to JSONL events
            if self._typing:
                if event.event_type in (EventType.TOOL_USE, EventType.TOOL_RESULT):
                    self._typing.start_typing(chat_id, topic_id)
                elif event.event_type == EventType.TEXT:
                    self._typing.stop_typing(chat_id, topic_id)

            # P1-M1: Tool pairing — track tool_use display text,
            # then edit the message with ✅/❌ when tool_result arrives
            if event.event_type == EventType.TOOL_USE:
                formatted = format_event_for_telegram(event)
                if not formatted:
                    continue
                logger.info("→ TOOL_USE: %s", formatted[:80])
                # Store for pairing with the future tool_result
                if event.tool_id:
                    self._pending_tools[event.tool_id] = formatted
                if self._queue:
                    await self._queue.enqueue(MessageTask(
                        chat_id=chat_id,
                        thread_id=topic_id,
                        text=formatted,
                        task_type=TaskType.TOOL_USE,
                        tool_id=event.tool_id,
                    ))

            elif event.event_type == EventType.TOOL_RESULT:
                original_text = self._pending_tools.pop(event.tool_id, None)
                if original_text and self._queue:
                    logger.info("→ TOOL_RESULT: %s (error=%s)", event.tool_id[:8] if event.tool_id else "?", event.is_error)
                    suffix = " ❌" if event.is_error else " ✅"
                    await self._queue.enqueue(MessageTask(
                        chat_id=chat_id,
                        thread_id=topic_id,
                        text=original_text + suffix,
                        task_type=TaskType.TOOL_RESULT,
                        tool_id=event.tool_id,
                    ))

            else:
                formatted = format_event_for_telegram(event)
                if not formatted:
                    continue
                logger.info("→ TEXT: %s", formatted[:80])
                if self._queue:
                    await self._queue.enqueue(MessageTask(
                        chat_id=chat_id,
                        thread_id=topic_id,
                        text=formatted,
                        task_type=TaskType.CONTENT,
                    ))

    # ------------------------------------------------------------------
    # Telegram sending
    # ------------------------------------------------------------------

    async def _send_telegram_message(
        self, chat_id: int, text: str, thread_id: int | None,
    ) -> int | None:
        """Send a message to Telegram with markdown formatting and fallback.

        Returns the message_id on success, None on failure.
        """
        if not self._app:
            return None

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
            result = await self._app.bot.send_message(**kwargs)
            return result.message_id
        except Exception:
            # Fallback: send without formatting (P1-MD3)
            kwargs.pop("parse_mode", None)
            kwargs["text"] = text
            try:
                result = await self._app.bot.send_message(**kwargs)
                return result.message_id
            except Exception as e:
                logger.error("Failed to send message: %s", e)
                return None

    async def _edit_telegram_message(
        self, chat_id: int, message_id: int, text: str, thread_id: int | None,
    ) -> None:
        """Edit an existing Telegram message with markdown formatting and fallback."""
        if not self._app:
            return

        formatted, parse_mode = to_telegram(text)

        kwargs = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": formatted or text,
        }
        if parse_mode:
            kwargs["parse_mode"] = parse_mode

        try:
            await self._app.bot.edit_message_text(**kwargs)
        except Exception:
            # Fallback: edit without formatting (P1-MD3)
            kwargs.pop("parse_mode", None)
            kwargs["text"] = text
            try:
                await self._app.bot.edit_message_text(**kwargs)
            except Exception as e:
                logger.error("Failed to edit message: %s", e)

    async def _delete_telegram_message(
        self, chat_id: int, message_id: int, thread_id: int | None,
    ) -> None:
        """Delete a Telegram message."""
        if not self._app:
            return

        try:
            await self._app.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.debug("Failed to delete message: %s", e)
