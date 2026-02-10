"""Async tmux bridge via libtmux.

One tmux session "metroclaude" with one window per Claude Code session.
Each window runs a `claude` process in a specific working directory.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import libtmux

from .config import get_settings
from .exceptions import TmuxError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class TmuxManager:
    """Manage tmux session and windows for Claude Code instances."""

    def __init__(self) -> None:
        self._server: libtmux.Server | None = None
        self._session: libtmux.Session | None = None
        self._settings = get_settings()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def ensure_session(self) -> libtmux.Session:
        """Get or create the main tmux session."""
        if self._session is not None:
            return self._session
        return await asyncio.to_thread(self._ensure_session_sync)

    def _ensure_session_sync(self) -> libtmux.Session:
        self._server = libtmux.Server()
        name = self._settings.tmux_session_name
        try:
            self._session = self._server.sessions.get(session_name=name)
            logger.info("Attached to existing tmux session '%s'", name)
        except Exception:
            self._session = self._server.new_session(
                session_name=name, attach=False,
            )
            logger.info("Created tmux session '%s'", name)
        return self._session

    # ------------------------------------------------------------------
    # Window management
    # ------------------------------------------------------------------

    async def create_window(
        self,
        window_name: str,
        working_dir: str | None = None,
        claude_session_id: str | None = None,
    ) -> libtmux.Window:
        """Create a new tmux window and start Claude Code in it."""
        session = await self.ensure_session()
        cwd = working_dir or str(self._settings.working_dir)
        claude_cmd = self._settings.claude_command

        def _create() -> libtmux.Window:
            # Check if window already exists
            existing = session.windows.filter(window_name=window_name)
            if existing:
                logger.info("Window '%s' already exists, reusing", window_name)
                return existing[0]

            window = session.new_window(
                window_name=window_name,
                attach=False,
                start_directory=cwd,
            )
            logger.info("Created window '%s' in %s", window_name, cwd)

            # Start Claude Code in the pane
            pane = window.active_pane
            if pane is None:
                raise TmuxError(f"No active pane in window '{window_name}'")

            cmd = claude_cmd
            if claude_session_id:
                cmd += f" --resume {claude_session_id}"

            pane.send_keys(cmd, enter=True)
            logger.info("Started '%s' in window '%s'", cmd, window_name)
            return window

        return await asyncio.to_thread(_create)

    async def kill_window(self, window_name: str) -> None:
        """Kill a tmux window by name."""
        session = await self.ensure_session()

        def _kill() -> None:
            windows = session.windows.filter(window_name=window_name)
            if windows:
                windows[0].kill()
                logger.info("Killed window '%s'", window_name)

        await asyncio.to_thread(_kill)

    async def list_windows(self) -> list[str]:
        """List all window names in the session."""
        session = await self.ensure_session()

        def _list() -> list[str]:
            return [w.window_name for w in session.windows if w.window_name]

        return await asyncio.to_thread(_list)

    # ------------------------------------------------------------------
    # Input — send keystrokes to Claude
    # ------------------------------------------------------------------

    async def send_text(self, window_name: str, text: str) -> None:
        """Send text to a window's active pane using literal flag (anti-injection)."""
        session = await self.ensure_session()

        def _send() -> None:
            windows = session.windows.filter(window_name=window_name)
            if not windows:
                raise TmuxError(f"Window '{window_name}' not found")
            pane = windows[0].active_pane
            if pane is None:
                raise TmuxError(f"No active pane in window '{window_name}'")
            # Use send_keys with literal=True equivalent: -l flag
            # libtmux send_keys sends literal text by default
            pane.send_keys(text, enter=False)

        await asyncio.to_thread(_send)
        # Small delay to let the terminal process the text
        await asyncio.sleep(0.3)

    async def send_enter(self, window_name: str) -> None:
        """Send Enter key to a window."""
        session = await self.ensure_session()

        def _enter() -> None:
            windows = session.windows.filter(window_name=window_name)
            if not windows:
                raise TmuxError(f"Window '{window_name}' not found")
            pane = windows[0].active_pane
            if pane:
                pane.enter()

        await asyncio.to_thread(_enter)

    async def send_keys_raw(self, window_name: str, keys: str) -> None:
        """Send raw tmux key names (e.g. 'Escape', 'C-c', 'y')."""
        session = await self.ensure_session()

        def _send() -> None:
            windows = session.windows.filter(window_name=window_name)
            if not windows:
                raise TmuxError(f"Window '{window_name}' not found")
            pane = windows[0].active_pane
            if pane:
                pane.cmd("send-keys", keys)

        await asyncio.to_thread(_send)

    async def send_message(self, window_name: str, text: str) -> None:
        """Send a complete message: text + Enter, with delay between."""
        await self.send_text(window_name, text)
        await asyncio.sleep(0.5)  # Let Claude process the text before Enter
        await self.send_enter(window_name)

    # ------------------------------------------------------------------
    # Output — capture terminal content
    # ------------------------------------------------------------------

    async def capture_pane(self, window_name: str, history: int = 0) -> str:
        """Capture the visible content of a window's pane.

        Args:
            window_name: Name of the tmux window.
            history: Number of history lines to include (0 = visible only).
        """
        session = await self.ensure_session()

        def _capture() -> str:
            windows = session.windows.filter(window_name=window_name)
            if not windows:
                raise TmuxError(f"Window '{window_name}' not found")
            pane = windows[0].active_pane
            if pane is None:
                raise TmuxError(f"No active pane in window '{window_name}'")
            lines = pane.capture_pane()
            return "\n".join(lines) if isinstance(lines, list) else str(lines)

        return await asyncio.to_thread(_capture)

    async def get_pane_pid(self, window_name: str) -> int | None:
        """Get the PID of the process running in a window's pane."""
        session = await self.ensure_session()

        def _pid() -> int | None:
            windows = session.windows.filter(window_name=window_name)
            if not windows:
                return None
            pane = windows[0].active_pane
            if pane is None:
                return None
            try:
                return int(pane.pane_pid)
            except (ValueError, TypeError):
                return None

        return await asyncio.to_thread(_pid)
