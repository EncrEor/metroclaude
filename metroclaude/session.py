"""Session manager — maps Telegram topics to tmux windows and Claude sessions.

State is persisted to ~/.metroclaude/state.json so sessions survive bot restarts.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """State for one Telegram topic ↔ Claude session binding."""
    topic_id: int  # Telegram message_thread_id
    chat_id: int  # Telegram group chat ID
    window_name: str  # tmux window name
    working_dir: str  # Absolute path to project directory
    claude_session_id: str = ""  # Claude Code session UUID (set by hook)
    created_at: float = 0.0
    last_active: float = 0.0
    is_running: bool = False

    def __post_init__(self) -> None:
        now = time.time()
        if not self.created_at:
            self.created_at = now
        if not self.last_active:
            self.last_active = now

    def touch(self) -> None:
        self.last_active = time.time()


@dataclass
class RecentSession:
    """Lightweight reference to a past session for /resume UI."""
    session_id: str
    window_name: str
    working_dir: str
    timestamp: float


class SessionManager:
    """Manage all active and recent sessions with JSON persistence."""

    MAX_RECENT = 5

    def __init__(self) -> None:
        self._settings = get_settings()
        self._state_file = self._settings.state_dir / "state.json"
        self._sessions: dict[str, SessionInfo] = {}  # key = "chatid:topicid"
        self._recent: list[RecentSession] = []
        self._load()

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key(chat_id: int, topic_id: int) -> str:
        return f"{chat_id}:{topic_id}"

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get(self, chat_id: int, topic_id: int) -> SessionInfo | None:
        return self._sessions.get(self._key(chat_id, topic_id))

    def create(
        self,
        chat_id: int,
        topic_id: int,
        window_name: str,
        working_dir: str,
    ) -> SessionInfo:
        key = self._key(chat_id, topic_id)
        info = SessionInfo(
            topic_id=topic_id,
            chat_id=chat_id,
            window_name=window_name,
            working_dir=working_dir,
            is_running=True,
        )
        self._sessions[key] = info
        self._save()
        logger.info("Session created: %s → window '%s'", key, window_name)
        return info

    def update_claude_session(
        self, chat_id: int, topic_id: int, claude_session_id: str,
    ) -> None:
        info = self.get(chat_id, topic_id)
        if info:
            info.claude_session_id = claude_session_id
            info.touch()
            self._save()

    def remove(self, chat_id: int, topic_id: int) -> SessionInfo | None:
        key = self._key(chat_id, topic_id)
        info = self._sessions.pop(key, None)
        if info and info.claude_session_id:
            self._add_recent(RecentSession(
                session_id=info.claude_session_id,
                window_name=info.window_name,
                working_dir=info.working_dir,
                timestamp=time.time(),
            ))
        self._save()
        return info

    def all_sessions(self) -> list[SessionInfo]:
        return list(self._sessions.values())

    def find_by_window(self, window_name: str) -> SessionInfo | None:
        for info in self._sessions.values():
            if info.window_name == window_name:
                return info
        return None

    def find_by_claude_session(self, claude_session_id: str) -> SessionInfo | None:
        for info in self._sessions.values():
            if info.claude_session_id == claude_session_id:
                return info
        return None

    # P1-S4: Clear session by window name
    def clear_window_session(self, window_name: str) -> SessionInfo | None:
        """Remove the session associated with a window name.

        Returns the removed SessionInfo, or None if not found.
        """
        for key, info in list(self._sessions.items()):
            if info.window_name == window_name:
                return self.remove(info.chat_id, info.topic_id)
        return None

    # ------------------------------------------------------------------
    # P1-S3+SEC10: Stale binding detection
    # ------------------------------------------------------------------

    def cleanup_stale_sessions(self, live_windows: set[str]) -> list[SessionInfo]:
        """Remove sessions whose tmux windows no longer exist.

        Call periodically with the set of currently alive window names.
        Returns the list of removed sessions.
        """
        stale: list[SessionInfo] = []
        for key, info in list(self._sessions.items()):
            if info.window_name not in live_windows:
                stale.append(info)
        for info in stale:
            logger.warning(
                "Stale session cleaned: %s (window '%s' gone)",
                self._key(info.chat_id, info.topic_id),
                info.window_name,
            )
            self.remove(info.chat_id, info.topic_id)
        return stale

    # ------------------------------------------------------------------
    # Recent sessions (for /resume)
    # ------------------------------------------------------------------

    def recent_sessions(self) -> list[RecentSession]:
        return list(self._recent)

    def _add_recent(self, entry: RecentSession) -> None:
        # Deduplicate by session_id
        self._recent = [r for r in self._recent if r.session_id != entry.session_id]
        self._recent.insert(0, entry)
        self._recent = self._recent[: self.MAX_RECENT]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save(self) -> None:
        data = {
            "sessions": {k: asdict(v) for k, v in self._sessions.items()},
            "recent": [asdict(r) for r in self._recent],
        }
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write: temp file + os.replace() (P1-S1)
            fd, tmp_path = tempfile.mkstemp(
                dir=self._state_file.parent,
                prefix=".state_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self._state_file)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as e:
            logger.error("Failed to save state: %s", e)

    def _load(self) -> None:
        if not self._state_file.exists():
            return
        try:
            data = json.loads(self._state_file.read_text())
            for k, v in data.get("sessions", {}).items():
                self._sessions[k] = SessionInfo(**v)
            for r in data.get("recent", []):
                self._recent.append(RecentSession(**r))
            logger.info(
                "Loaded %d sessions, %d recent",
                len(self._sessions), len(self._recent),
            )
        except Exception as e:
            logger.warning("Failed to load state: %s", e)
