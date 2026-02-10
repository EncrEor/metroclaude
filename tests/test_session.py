"""Tests for Batch 7 — session manager and monitor improvements."""

import json
import time
from pathlib import Path
from unittest.mock import patch

from metroclaude.session import SessionInfo, SessionManager, RecentSession


# ------------------------------------------------------------------
# P1-S4: clear_window_session()
# ------------------------------------------------------------------

def test_clear_window_session_found():
    """Should remove and return session by window name."""
    with patch.object(SessionManager, "_load"):
        mgr = SessionManager()
        mgr._sessions = {}
        mgr._recent = []
        info = mgr.create(100, 200, "my-window", "/tmp")
        assert mgr.get(100, 200) is not None

        removed = mgr.clear_window_session("my-window")
        assert removed is not None
        assert removed.window_name == "my-window"
        assert mgr.get(100, 200) is None


def test_clear_window_session_not_found():
    """Should return None when window doesn't exist."""
    with patch.object(SessionManager, "_load"):
        mgr = SessionManager()
        mgr._sessions = {}
        mgr._recent = []
        result = mgr.clear_window_session("nonexistent")
        assert result is None


# ------------------------------------------------------------------
# P1-S3 + P1-SEC10: Stale binding detection
# ------------------------------------------------------------------

def test_cleanup_stale_sessions_removes_dead():
    """Should remove sessions whose windows are gone."""
    with patch.object(SessionManager, "_load"):
        mgr = SessionManager()
        mgr._sessions = {}
        mgr._recent = []
        mgr.create(100, 1, "alive-window", "/tmp")
        mgr.create(100, 2, "dead-window", "/tmp")
        mgr.create(100, 3, "also-dead", "/tmp")

        stale = mgr.cleanup_stale_sessions({"alive-window"})
        assert len(stale) == 2
        assert {s.window_name for s in stale} == {"dead-window", "also-dead"}
        # Alive session should remain
        assert mgr.get(100, 1) is not None
        assert mgr.get(100, 2) is None
        assert mgr.get(100, 3) is None


def test_cleanup_stale_sessions_none_stale():
    """Should return empty list when all windows are alive."""
    with patch.object(SessionManager, "_load"):
        mgr = SessionManager()
        mgr._sessions = {}
        mgr._recent = []
        mgr.create(100, 1, "win-a", "/tmp")
        mgr.create(100, 2, "win-b", "/tmp")

        stale = mgr.cleanup_stale_sessions({"win-a", "win-b"})
        assert stale == []
        assert len(mgr.all_sessions()) == 2


def test_cleanup_stale_sessions_adds_to_recent():
    """Stale sessions with claude_session_id should be added to recent."""
    with patch.object(SessionManager, "_load"):
        mgr = SessionManager()
        mgr._sessions = {}
        mgr._recent = []
        info = mgr.create(100, 1, "dead-win", "/projects/foo")
        info.claude_session_id = "abc-123"

        stale = mgr.cleanup_stale_sessions(set())
        assert len(stale) == 1
        recent = mgr.recent_sessions()
        assert len(recent) == 1
        assert recent[0].session_id == "abc-123"


# ------------------------------------------------------------------
# P1-M2: mtime <= instead of ==
# ------------------------------------------------------------------

def test_monitor_mtime_uses_lte():
    """Verify monitor.py uses <= for mtime comparison (not ==)."""
    import inspect
    from metroclaude.monitor import SessionMonitor
    source = inspect.getsource(SessionMonitor.poll)
    assert "<= self._file.last_mtime" in source
    assert "== self._file.last_mtime" not in source


# ------------------------------------------------------------------
# P1-S5: cleanup_stale_map_entries
# ------------------------------------------------------------------

def test_cleanup_stale_map_entries(tmp_path):
    """Should remove entries for dead windows from session_map."""
    from metroclaude.hooks import (
        SESSION_MAP_FILE,
        cleanup_stale_map_entries,
        read_session_map,
        write_session_map,
    )

    # Use monkeypatch for the file path
    import metroclaude.hooks as hooks_mod
    original_file = hooks_mod.SESSION_MAP_FILE
    original_lock = hooks_mod.SESSION_MAP_LOCK
    try:
        hooks_mod.SESSION_MAP_FILE = tmp_path / "session_map.json"
        hooks_mod.SESSION_MAP_LOCK = tmp_path / "session_map.lock"

        # Write some entries
        write_session_map({
            "metroclaude:alive-win": {"session_id": "s1", "cwd": "/tmp"},
            "metroclaude:dead-win": {"session_id": "s2", "cwd": "/tmp"},
        })

        removed = cleanup_stale_map_entries({"alive-win"})
        assert removed == 1

        data = read_session_map()
        assert "metroclaude:alive-win" in data
        assert "metroclaude:dead-win" not in data
    finally:
        hooks_mod.SESSION_MAP_FILE = original_file
        hooks_mod.SESSION_MAP_LOCK = original_lock


# ------------------------------------------------------------------
# P1-M1: Tool pairing (_pending_tools)
# ------------------------------------------------------------------

def test_pending_tools_attribute_exists():
    """MetroClaudeBot should have _pending_tools dict."""
    from metroclaude.bot import MetroClaudeBot
    bot = MetroClaudeBot()
    assert hasattr(bot, "_pending_tools")
    assert isinstance(bot._pending_tools, dict)
