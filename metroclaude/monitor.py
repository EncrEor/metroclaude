"""JSONL monitor — poll Claude Code session files with byte-offset tracking.

Watches ~/.claude/projects/[project-hash]/[session-id].jsonl files for new events.
Uses byte-offset to never re-read what's already been processed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Callable

from .config import get_settings
from .parser import ParsedEvent, parse_jsonl_line

logger = logging.getLogger(__name__)


@dataclass
class MonitoredFile:
    """Track state for a single JSONL file."""
    path: Path
    byte_offset: int = 0
    last_mtime: float = 0.0
    partial_line: str = ""  # Incomplete JSON at EOF


@dataclass
class SessionMonitor:
    """Monitor JSONL files for a specific Claude Code session."""
    session_id: str
    project_dir: Path
    _file: MonitoredFile | None = field(default=None, init=False)

    @property
    def jsonl_path(self) -> Path:
        return self.project_dir / f"{self.session_id}.jsonl"

    def poll(self) -> list[ParsedEvent]:
        """Check for new events. Returns empty list if nothing new."""
        path = self.jsonl_path
        if not path.exists():
            return []

        try:
            stat = path.stat()
        except OSError:
            return []

        if self._file is None:
            self._file = MonitoredFile(path=path)

        # Skip if file hasn't changed (mtime check is cheap)
        # P1-M2: Use <= instead of == to avoid skipping on same-second writes
        if stat.st_mtime <= self._file.last_mtime:
            return []
        self._file.last_mtime = stat.st_mtime

        # Detect file truncation (e.g. after /clear)
        file_size = stat.st_size
        if file_size < self._file.byte_offset:
            logger.info(
                "File truncated for %s (offset %d > size %d), resetting",
                path.name, self._file.byte_offset, file_size,
            )
            self._file.byte_offset = 0
            self._file.partial_line = ""

        # Skip if no new bytes
        if file_size <= self._file.byte_offset:
            return []

        events: list[ParsedEvent] = []
        try:
            with open(path, "rb") as f:
                f.seek(self._file.byte_offset)
                new_data = f.read()
                self._file.byte_offset = f.tell()
        except OSError as e:
            logger.warning("Error reading %s: %s", path, e)
            return []

        text = self._file.partial_line + new_data.decode("utf-8", errors="replace")
        lines = text.split("\n")

        # Last element might be incomplete — save for next cycle
        self._file.partial_line = lines[-1] if lines else ""
        complete_lines = lines[:-1]

        for line in complete_lines:
            line = line.strip()
            if not line:
                continue
            parsed = parse_jsonl_line(line)
            events.extend(parsed)

        return events

    def skip_to_end(self) -> None:
        """Skip to end of file (used on session attach to avoid replaying history)."""
        path = self.jsonl_path
        if not path.exists():
            return
        try:
            size = path.stat().st_size
            if self._file is None:
                self._file = MonitoredFile(path=path)
            self._file.byte_offset = size
            self._file.last_mtime = path.stat().st_mtime
            self._file.partial_line = ""
            logger.info("Skipped to end of %s (offset=%d)", path.name, size)
        except OSError:
            pass


class MonitorPool:
    """Manage multiple session monitors and dispatch events via callbacks."""

    def __init__(self) -> None:
        self._monitors: dict[str, SessionMonitor] = {}
        self._callbacks: list[Callable[[str, list[ParsedEvent]], None]] = []
        self._running = False
        self._task: asyncio.Task | None = None
        self._settings = get_settings()

    def add_session(
        self,
        session_id: str,
        project_dir: Path | None = None,
        skip_existing: bool = True,
    ) -> SessionMonitor:
        """Register a new session to monitor."""
        if session_id in self._monitors:
            return self._monitors[session_id]

        if project_dir is None:
            project_dir = self._find_project_dir(session_id)

        monitor = SessionMonitor(session_id=session_id, project_dir=project_dir)
        if skip_existing:
            monitor.skip_to_end()
        self._monitors[session_id] = monitor
        logger.info("Monitoring session %s in %s", session_id, project_dir)
        return monitor

    def remove_session(self, session_id: str) -> None:
        """Stop monitoring a session."""
        self._monitors.pop(session_id, None)

    def on_events(self, callback: Callable[[str, list[ParsedEvent]], None]) -> None:
        """Register a callback for new events. Called with (session_id, events)."""
        self._callbacks.append(callback)

    async def start(self) -> None:
        """Start the polling loop."""
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Monitor pool started (interval=%.1fs)", self._settings.monitor_poll_interval)

    async def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Monitor pool stopped")

    async def _poll_loop(self) -> None:
        """Main polling loop — check all monitored files."""
        while self._running:
            for session_id, monitor in list(self._monitors.items()):
                try:
                    events = await asyncio.to_thread(monitor.poll)
                    if events:
                        logger.info("Polled %d event(s) from session %s", len(events), session_id)
                        for cb in self._callbacks:
                            try:
                                cb(session_id, events)
                            except Exception:
                                logger.exception("Callback error for session %s", session_id)
                except Exception:
                    logger.exception("Poll error for session %s", session_id)

            await asyncio.sleep(self._settings.monitor_poll_interval)

    def _find_project_dir(self, session_id: str) -> Path:
        """Find which project directory contains this session ID.

        Strategy:
        1. Scan all project dirs for the exact JSONL file
        2. Fall back to working_dir-derived project dir (even if JSONL
           doesn't exist yet — Claude creates it after first interaction)
        """
        projects_dir = self._settings.claude_projects_dir
        if not projects_dir.exists():
            raise FileNotFoundError(f"Claude projects dir not found: {projects_dir}")

        # 1. Exact match: JSONL file exists
        for project in projects_dir.iterdir():
            if project.is_dir():
                jsonl = project / f"{session_id}.jsonl"
                if jsonl.exists():
                    return project

        # 2. Derive from working_dir — Claude Code normalizes paths by
        #    replacing all non-alphanumeric chars with '-'
        import re
        wd = str(self._settings.working_dir)
        normalized = re.sub(r"[^a-zA-Z0-9]", "-", wd)
        default = projects_dir / normalized
        if default.is_dir():
            logger.debug("Using derived project dir: %s", default)
            return default

        # 3. Last resort: return the derived path anyway (JSONL will appear later)
        logger.warning(
            "Project dir %s not found for session %s, using it anyway (JSONL pending)",
            default, session_id,
        )
        return default
