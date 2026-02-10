"""Async tmux bridge via libtmux.

One tmux session "metroclaude" with one window per Claude Code session.
Each window runs a `claude` process in a specific working directory.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import libtmux

from .config import get_settings
from .exceptions import TmuxError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# P1-T1+T7: TmuxWindow dataclass (pattern from ccbot)
# ------------------------------------------------------------------

@dataclass
class TmuxWindow:
    """Information about a tmux window."""

    window_id: str
    window_name: str
    cwd: str = ""
    pane_current_command: str = ""


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
    # P1-T4: Internal helpers — DRY window+pane lookup
    # ------------------------------------------------------------------

    def _get_window_pane(
        self, session: libtmux.Session, window_name: str,
    ) -> tuple[libtmux.Window, libtmux.Pane]:
        """Lookup window by name and return (window, active_pane).

        Raises TmuxError if window or pane not found.
        """
        windows = session.windows.filter(window_name=window_name)
        if not windows:
            raise TmuxError(f"Window '{window_name}' not found")
        pane = windows[0].active_pane
        if pane is None:
            raise TmuxError(f"No active pane in window '{window_name}'")
        return windows[0], pane

    def _find_window(
        self, session: libtmux.Session, window_name: str,
    ) -> libtmux.Window | None:
        """Lookup window by name, return None if not found."""
        windows = session.windows.filter(window_name=window_name)
        return windows[0] if windows else None

    # ------------------------------------------------------------------
    # Window management
    # ------------------------------------------------------------------

    async def create_window(
        self,
        window_name: str,
        working_dir: str | None = None,
        claude_session_id: str | None = None,
    ) -> libtmux.Window:
        """Create a new tmux window and start Claude Code in it.

        P1-T6: Validates that working directory exists.
        P1-T3: Handles duplicate names with incremental suffix.

        Returns the created libtmux.Window (use window.window_name for
        the actual name, which may differ from the requested name if a
        suffix was added).
        """
        session = await self.ensure_session()
        cwd = working_dir or str(self._settings.working_dir)

        # P1-T6: Validate directory exists
        resolved = Path(cwd).expanduser().resolve()
        if not resolved.is_dir():
            raise TmuxError(f"Directory does not exist: {cwd}")
        cwd = str(resolved)

        claude_cmd = self._settings.claude_command

        def _create() -> libtmux.Window:
            # P1-T3: Handle duplicate window names with incremental suffix
            final_name = window_name
            if self._find_window(session, final_name):
                counter = 2
                while self._find_window(session, f"{window_name}-{counter}"):
                    counter += 1
                final_name = f"{window_name}-{counter}"
                logger.info(
                    "Window '%s' exists, using '%s'", window_name, final_name,
                )

            window = session.new_window(
                window_name=final_name,
                attach=False,
                start_directory=cwd,
            )
            logger.info("Created window '%s' in %s", final_name, cwd)

            # Start Claude Code in the pane
            pane = window.active_pane
            if pane is None:
                raise TmuxError(f"No active pane in window '{final_name}'")

            cmd = claude_cmd
            if claude_session_id:
                cmd += f" --resume {claude_session_id}"

            pane.send_keys(cmd, enter=True)
            logger.info("Started '%s' in window '%s'", cmd, final_name)
            return window

        return await asyncio.to_thread(_create)

    async def kill_window(self, window_name: str) -> None:
        """Kill a tmux window by name."""
        session = await self.ensure_session()

        def _kill() -> None:
            w = self._find_window(session, window_name)
            if w:
                w.kill()
                logger.info("Killed window '%s'", window_name)

        await asyncio.to_thread(_kill)

    async def list_windows(self) -> list[TmuxWindow]:
        """List all windows with enriched info (P1-T1+T7).

        Returns TmuxWindow dataclasses with window_id, name, cwd,
        and pane_current_command.
        """
        session = await self.ensure_session()

        def _list() -> list[TmuxWindow]:
            result: list[TmuxWindow] = []
            for w in session.windows:
                name = w.window_name or ""
                try:
                    pane = w.active_pane
                    cwd = pane.pane_current_path or "" if pane else ""
                    cmd = pane.pane_current_command or "" if pane else ""
                except Exception:
                    cwd = ""
                    cmd = ""
                result.append(TmuxWindow(
                    window_id=w.window_id or "",
                    window_name=name,
                    cwd=cwd,
                    pane_current_command=cmd,
                ))
            return result

        return await asyncio.to_thread(_list)

    # ------------------------------------------------------------------
    # P1-T5: Restart Claude Code in-place (after crash)
    # ------------------------------------------------------------------

    async def restart_claude(
        self, window_name: str, session_id: str | None = None,
    ) -> None:
        """Restart Claude Code in a window (e.g., after crash).

        Sends Escape -> /exit -> waits -> starts claude [--resume session_id].
        """
        # Send Escape to cancel any in-progress input
        await self.send_keys_raw(window_name, "Escape")
        await asyncio.sleep(0.5)

        # Try to exit cleanly first
        await self.send_message(window_name, "/exit")
        await asyncio.sleep(1.0)

        # Start claude again
        cmd = self._settings.claude_command
        if session_id:
            cmd += f" --resume {session_id}"

        await self.send_message(window_name, cmd)
        logger.info(
            "Restarted Claude in window '%s' (resume=%s)",
            window_name, session_id,
        )

    # ------------------------------------------------------------------
    # Input — send keystrokes to Claude
    # ------------------------------------------------------------------

    async def send_text(self, window_name: str, text: str) -> None:
        """Send text to a window's active pane using literal flag (anti-injection)."""
        session = await self.ensure_session()

        def _send() -> None:
            _, pane = self._get_window_pane(session, window_name)
            pane.send_keys(text, enter=False)

        await asyncio.to_thread(_send)
        # Small delay to let the terminal process the text
        await asyncio.sleep(0.3)

    async def send_enter(self, window_name: str) -> None:
        """Send Enter key to a window."""
        session = await self.ensure_session()

        def _enter() -> None:
            _, pane = self._get_window_pane(session, window_name)
            pane.enter()

        await asyncio.to_thread(_enter)

    async def send_keys_raw(self, window_name: str, keys: str) -> None:
        """Send raw tmux key names (e.g. 'Escape', 'C-c', 'y')."""
        session = await self.ensure_session()

        def _send() -> None:
            _, pane = self._get_window_pane(session, window_name)
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

    async def capture_pane(self, window_name: str) -> str:
        """Capture the visible content of a window's pane.

        P1-T2: Removed unused ``history`` parameter.
        """
        session = await self.ensure_session()

        def _capture() -> str:
            _, pane = self._get_window_pane(session, window_name)
            lines = pane.capture_pane()
            return "\n".join(lines) if isinstance(lines, list) else str(lines)

        return await asyncio.to_thread(_capture)

    async def get_pane_current_command(self, window_name: str) -> str | None:
        """Get the foreground command name of a window's pane.

        Used by status polling to detect Claude exit (returns "bash"/"zsh"
        when Claude exits). Returns None if window not found.
        """
        session = await self.ensure_session()

        def _cmd() -> str | None:
            try:
                _, pane = self._get_window_pane(session, window_name)
                return pane.pane_current_command
            except TmuxError:
                return None

        return await asyncio.to_thread(_cmd)

    async def get_pane_pid(self, window_name: str) -> int | None:
        """Get the PID of the process running in a window's pane."""
        session = await self.ensure_session()

        def _pid() -> int | None:
            try:
                _, pane = self._get_window_pane(session, window_name)
                return int(pane.pane_pid)
            except (TmuxError, ValueError, TypeError):
                return None

        return await asyncio.to_thread(_pid)
