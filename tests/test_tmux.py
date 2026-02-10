"""Tests for Batch 5 — tmux.py improvements."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from metroclaude.tmux import TmuxManager, TmuxWindow, TmuxError


# ------------------------------------------------------------------
# P1-T1+T7: TmuxWindow dataclass
# ------------------------------------------------------------------

def test_tmux_window_dataclass():
    """TmuxWindow should store window metadata."""
    w = TmuxWindow(
        window_id="@1",
        window_name="test-window",
        cwd="/home/user/project",
        pane_current_command="claude",
    )
    assert w.window_id == "@1"
    assert w.window_name == "test-window"
    assert w.cwd == "/home/user/project"
    assert w.pane_current_command == "claude"


def test_tmux_window_defaults():
    """TmuxWindow should have empty defaults for optional fields."""
    w = TmuxWindow(window_id="@1", window_name="test")
    assert w.cwd == ""
    assert w.pane_current_command == ""


# ------------------------------------------------------------------
# P1-T4: _get_window_pane helper
# ------------------------------------------------------------------

def test_get_window_pane_found():
    """_get_window_pane should return (window, pane) tuple."""
    mgr = _make_manager()
    session = MagicMock()
    pane = MagicMock()
    window = MagicMock()
    window.active_pane = pane
    session.windows.filter.return_value = [window]

    result_window, result_pane = mgr._get_window_pane(session, "test")
    assert result_window is window
    assert result_pane is pane


def test_get_window_pane_not_found():
    """_get_window_pane should raise TmuxError if window missing."""
    mgr = _make_manager()
    session = MagicMock()
    session.windows.filter.return_value = []

    with pytest.raises(TmuxError, match="not found"):
        mgr._get_window_pane(session, "nonexistent")


def test_get_window_pane_no_pane():
    """_get_window_pane should raise TmuxError if no active pane."""
    mgr = _make_manager()
    session = MagicMock()
    window = MagicMock()
    window.active_pane = None
    session.windows.filter.return_value = [window]

    with pytest.raises(TmuxError, match="No active pane"):
        mgr._get_window_pane(session, "test")


# ------------------------------------------------------------------
# P1-T4: _find_window helper
# ------------------------------------------------------------------

def test_find_window_found():
    """_find_window should return the window if it exists."""
    mgr = _make_manager()
    session = MagicMock()
    window = MagicMock()
    session.windows.filter.return_value = [window]

    assert mgr._find_window(session, "test") is window


def test_find_window_not_found():
    """_find_window should return None if window doesn't exist."""
    mgr = _make_manager()
    session = MagicMock()
    session.windows.filter.return_value = []

    assert mgr._find_window(session, "nonexistent") is None


# ------------------------------------------------------------------
# P1-T6: Directory validation in create_window
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_window_invalid_dir():
    """create_window should raise TmuxError for non-existent directory."""
    mgr = _make_manager()
    mgr._session = MagicMock()  # Skip ensure_session

    with pytest.raises(TmuxError, match="Directory does not exist"):
        await mgr.create_window("test", "/nonexistent/path/12345")


@pytest.mark.asyncio
async def test_create_window_valid_dir(tmp_path):
    """create_window should accept valid directory."""
    mgr = _make_manager()
    session = MagicMock()
    mgr._session = session

    # Mock the window creation
    window = MagicMock()
    window.window_name = "test"
    pane = MagicMock()
    window.active_pane = pane
    session.new_window.return_value = window
    session.windows.filter.return_value = []  # No duplicates

    result = await mgr.create_window("test", str(tmp_path))
    assert result is window
    session.new_window.assert_called_once()


# ------------------------------------------------------------------
# P1-T3: Duplicate window name handling
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_window_duplicate_suffix(tmp_path):
    """create_window should add -2 suffix when name exists."""
    mgr = _make_manager()
    session = MagicMock()
    mgr._session = session

    # First call: "test" exists, "test-2" doesn't
    existing_window = MagicMock()
    new_window = MagicMock()
    new_window.window_name = "test-2"
    new_window.active_pane = MagicMock()

    def filter_side_effect(window_name=None):
        if window_name == "test":
            return [existing_window]
        return []

    session.windows.filter.side_effect = filter_side_effect
    session.new_window.return_value = new_window

    result = await mgr.create_window("test", str(tmp_path))
    # Should have created with name "test-2"
    session.new_window.assert_called_once()
    call_kwargs = session.new_window.call_args
    assert call_kwargs.kwargs.get("window_name") == "test-2" or \
           (call_kwargs.args and "test-2" in str(call_kwargs))


@pytest.mark.asyncio
async def test_create_window_duplicate_suffix_increments(tmp_path):
    """create_window should increment suffix (test-2, test-3, etc.)."""
    mgr = _make_manager()
    session = MagicMock()
    mgr._session = session

    new_window = MagicMock()
    new_window.window_name = "test-3"
    new_window.active_pane = MagicMock()

    # "test" and "test-2" exist, "test-3" doesn't
    def filter_side_effect(window_name=None):
        if window_name in ("test", "test-2"):
            return [MagicMock()]
        return []

    session.windows.filter.side_effect = filter_side_effect
    session.new_window.return_value = new_window

    result = await mgr.create_window("test", str(tmp_path))
    call_kwargs = session.new_window.call_args
    assert call_kwargs.kwargs.get("window_name") == "test-3"


# ------------------------------------------------------------------
# P1-T2: capture_pane without history param
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_capture_pane_no_history_param():
    """capture_pane should work without history parameter."""
    mgr = _make_manager()
    session = MagicMock()
    mgr._session = session

    pane = MagicMock()
    pane.capture_pane.return_value = ["line1", "line2", "line3"]
    window = MagicMock()
    window.active_pane = pane
    session.windows.filter.return_value = [window]

    result = await mgr.capture_pane("test")
    assert result == "line1\nline2\nline3"


# ------------------------------------------------------------------
# P1-T5: restart_claude method
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_restart_claude_with_session_id():
    """restart_claude should send /exit then start claude --resume."""
    mgr = _make_manager()
    session = MagicMock()
    mgr._session = session

    pane = MagicMock()
    window = MagicMock()
    window.active_pane = pane
    session.windows.filter.return_value = [window]

    # Track sent messages
    sent = []
    original_send_keys = pane.send_keys
    def capture_send(text, enter=True):
        sent.append((text, enter))
    pane.send_keys.side_effect = capture_send
    pane.cmd = MagicMock()

    await mgr.restart_claude("test-window", session_id="abc-123")

    # Should have sent: Escape (via cmd), /exit (text+enter), claude --resume (text+enter)
    pane.cmd.assert_called()  # send_keys_raw sends via cmd
    # Check that claude --resume was sent
    assert any("--resume abc-123" in str(call) for call in pane.send_keys.call_args_list)


@pytest.mark.asyncio
async def test_restart_claude_without_session_id():
    """restart_claude without session_id should start fresh claude."""
    mgr = _make_manager()
    session = MagicMock()
    mgr._session = session

    pane = MagicMock()
    window = MagicMock()
    window.active_pane = pane
    session.windows.filter.return_value = [window]
    pane.cmd = MagicMock()

    await mgr.restart_claude("test-window")

    # Should NOT have --resume in any call
    for call in pane.send_keys.call_args_list:
        args_str = str(call)
        if "--resume" in args_str:
            pytest.fail("Should not have --resume without session_id")


# ------------------------------------------------------------------
# P1-T1+T7: list_windows returns TmuxWindow
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_windows_returns_tmux_window():
    """list_windows should return list of TmuxWindow dataclasses."""
    mgr = _make_manager()
    session = MagicMock()
    mgr._session = session

    pane1 = MagicMock()
    pane1.pane_current_path = "/home/user/project1"
    pane1.pane_current_command = "claude"

    pane2 = MagicMock()
    pane2.pane_current_path = "/home/user/project2"
    pane2.pane_current_command = "bash"

    win1 = MagicMock()
    win1.window_name = "proj1"
    win1.window_id = "@1"
    win1.active_pane = pane1

    win2 = MagicMock()
    win2.window_name = "proj2"
    win2.window_id = "@2"
    win2.active_pane = pane2

    session.windows = [win1, win2]

    result = await mgr.list_windows()
    assert len(result) == 2
    assert isinstance(result[0], TmuxWindow)
    assert result[0].window_name == "proj1"
    assert result[0].cwd == "/home/user/project1"
    assert result[0].pane_current_command == "claude"
    assert result[1].window_name == "proj2"
    assert result[1].cwd == "/home/user/project2"


# ------------------------------------------------------------------
# Input methods use _get_window_pane
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_text_uses_helper():
    """send_text should raise TmuxError for missing window."""
    mgr = _make_manager()
    session = MagicMock()
    mgr._session = session
    session.windows.filter.return_value = []

    with pytest.raises(TmuxError, match="not found"):
        await mgr.send_text("nonexistent", "hello")


@pytest.mark.asyncio
async def test_send_enter_uses_helper():
    """send_enter should raise TmuxError for missing window."""
    mgr = _make_manager()
    session = MagicMock()
    mgr._session = session
    session.windows.filter.return_value = []

    with pytest.raises(TmuxError, match="not found"):
        await mgr.send_enter("nonexistent")


@pytest.mark.asyncio
async def test_get_pane_current_command_returns_none():
    """get_pane_current_command should return None for missing window."""
    mgr = _make_manager()
    session = MagicMock()
    mgr._session = session
    session.windows.filter.return_value = []

    result = await mgr.get_pane_current_command("nonexistent")
    assert result is None


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def _make_manager() -> TmuxManager:
    """Create TmuxManager with mocked settings."""
    with patch("metroclaude.tmux.get_settings") as mock:
        settings = MagicMock()
        settings.tmux_session_name = "metroclaude"
        settings.working_dir = Path("/tmp")
        settings.claude_command = "claude"
        mock.return_value = settings
        return TmuxManager()
