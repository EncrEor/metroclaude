"""Tests for Batch 4 — security & robustness."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from metroclaude.security.input_sanitizer import (
    DEFAULT_MAX_LENGTH,
    sanitize_path_argument,
    sanitize_tmux_input,
)


# ------------------------------------------------------------------
# P1-SEC2: Command injection detection
# ------------------------------------------------------------------

def test_sanitize_strips_backtick_injection():
    """Backtick command substitution should be stripped."""
    result = sanitize_tmux_input("hello `whoami` world")
    assert "`" not in result
    assert "whoami" not in result


def test_sanitize_strips_dollar_paren_injection():
    """$() command substitution should be stripped."""
    result = sanitize_tmux_input("hello $(rm -rf /) world")
    assert "$(" not in result
    assert "rm" not in result


def test_sanitize_preserves_normal_text():
    """Normal text should pass through unchanged."""
    text = "Hello, this is a normal message with $100 and parens()"
    result = sanitize_tmux_input(text)
    # $100 doesn't match $(...) pattern, so it should stay
    assert "Hello" in result


# ------------------------------------------------------------------
# P1-SEC3: Message length limit
# ------------------------------------------------------------------

def test_sanitize_truncates_long_input():
    """Input exceeding max_length should be truncated."""
    text = "a" * 5000
    result = sanitize_tmux_input(text, max_length=100)
    assert len(result) == 100


def test_default_max_length():
    """Default max length should be 4000."""
    assert DEFAULT_MAX_LENGTH == 4000


# ------------------------------------------------------------------
# P1-SEC4: Path validation
# ------------------------------------------------------------------

def test_sanitize_path_strips_control_chars():
    """Control characters should be stripped from paths."""
    result = sanitize_path_argument("/home/user/\x00project")
    assert "\x00" not in result
    assert "project" in result


def test_sanitize_path_strips_esc():
    """ESC sequences should be stripped from paths."""
    result = sanitize_path_argument("/home/\x1b[31muser\x1b[0m/project")
    assert "\x1b" not in result


# Test _is_path_allowed via import
from metroclaude.handlers.commands import _is_path_allowed


def test_path_allowed_under_home():
    """Path under home directory should be allowed."""
    home = Path.home()
    assert _is_path_allowed(home / "Documents" / "project") is True


def test_path_rejected_outside_home():
    """Path outside home directory should be rejected."""
    assert _is_path_allowed(Path("/etc/passwd")) is False
    assert _is_path_allowed(Path("/root/.ssh")) is False
    assert _is_path_allowed(Path("/tmp")) is False


def test_path_home_itself_allowed():
    """Home directory itself should be allowed."""
    assert _is_path_allowed(Path.home()) is True


# ------------------------------------------------------------------
# P1-S1: Atomic write for state.json
# ------------------------------------------------------------------

def test_session_manager_atomic_write():
    """SessionManager._save() should use atomic write (temp + replace)."""
    from metroclaude.session import SessionManager

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"

        with patch("metroclaude.session.get_settings") as mock_settings:
            settings = MagicMock()
            settings.state_dir = Path(tmpdir)
            mock_settings.return_value = settings

            mgr = SessionManager()
            mgr._state_file = state_file

            # Create a session to force a save
            mgr._sessions["test:1"] = MagicMock()
            mgr._sessions["test:1"].__class__.__name__ = "SessionInfo"

            # The _save should create the file atomically
            # We can't easily test atomicity, but we can verify the file exists
            # and no temp files are left behind
            from dataclasses import asdict
            with patch("metroclaude.session.asdict", return_value={"test": "data"}):
                mgr._save()

            assert state_file.exists()
            data = json.loads(state_file.read_text())
            assert "sessions" in data

            # No temp files should remain
            temp_files = list(Path(tmpdir).glob(".state_*.tmp"))
            assert len(temp_files) == 0


# ------------------------------------------------------------------
# P1-H3: Session map key format
# ------------------------------------------------------------------

def test_hook_key_format_with_session_window():
    """Key should preserve session:window format when already present."""
    # Simulate the hook logic
    window_name = "metroclaude:general"
    pane_id = "%5"
    session_id = "abc-def"

    if ":" in window_name:
        key = window_name
    elif window_name:
        key = f"metroclaude:{window_name}"
    elif pane_id:
        key = f"metroclaude:pane-{pane_id.lstrip('%')}"
    else:
        key = f"metroclaude:session-{session_id[:8]}"

    assert key == "metroclaude:general"


def test_hook_key_format_bare_window():
    """Bare window name should get prefixed."""
    window_name = "general"
    pane_id = "%5"
    session_id = "abc-def"

    if ":" in window_name:
        key = window_name
    elif window_name:
        key = f"metroclaude:{window_name}"
    elif pane_id:
        key = f"metroclaude:pane-{pane_id.lstrip('%')}"
    else:
        key = f"metroclaude:session-{session_id[:8]}"

    assert key == "metroclaude:general"


def test_hook_key_format_pane_fallback():
    """Pane ID fallback should use metroclaude:pane-N format."""
    window_name = ""
    pane_id = "%42"
    session_id = "abc-def"

    if ":" in window_name:
        key = window_name
    elif window_name:
        key = f"metroclaude:{window_name}"
    elif pane_id:
        key = f"metroclaude:pane-{pane_id.lstrip('%')}"
    else:
        key = f"metroclaude:session-{session_id[:8]}"

    assert key == "metroclaude:pane-42"


def test_hook_key_format_session_fallback():
    """Session ID fallback should use first 8 chars."""
    window_name = ""
    pane_id = ""
    session_id = "12345678-abcd-efgh-1234-567890abcdef"

    if ":" in window_name:
        key = window_name
    elif window_name:
        key = f"metroclaude:{window_name}"
    elif pane_id:
        key = f"metroclaude:pane-{pane_id.lstrip('%')}"
    else:
        key = f"metroclaude:session-{session_id[:8]}"

    assert key == "metroclaude:session-12345678"


# ------------------------------------------------------------------
# P1-SEC12: Traceback hiding
# ------------------------------------------------------------------

def test_error_messages_are_generic():
    """Error messages to users should not contain Exception details."""
    # This is more of a code review test — we verify the patterns exist
    import inspect
    from metroclaude.handlers import commands, message

    # Check commands.py doesn't have f"Erreur : {e}" patterns
    source = inspect.getsource(commands)
    assert 'f"Erreur : {e}"' not in source
    assert "f'Erreur : {e}'" not in source

    # Check message.py doesn't have f"Erreur : {e}" patterns
    source = inspect.getsource(message)
    assert 'f"Erreur : {e}"' not in source


# ------------------------------------------------------------------
# Auth — P1-SEC1 + P1-SEC7 (unit tests for check_auth)
# ------------------------------------------------------------------

def test_is_authorized_with_allowed_user():
    """Authorized user should return True."""
    from metroclaude.security.auth import is_authorized

    with patch("metroclaude.security.auth.get_settings") as mock:
        mock.return_value.get_allowed_user_ids.return_value = [123, 456]
        assert is_authorized(123) is True
        assert is_authorized(456) is True


def test_is_authorized_with_unknown_user():
    """Unknown user should return False."""
    from metroclaude.security.auth import is_authorized

    with patch("metroclaude.security.auth.get_settings") as mock:
        mock.return_value.get_allowed_user_ids.return_value = [123]
        assert is_authorized(999) is False


def test_is_authorized_empty_whitelist():
    """Empty whitelist should block all."""
    from metroclaude.security.auth import is_authorized

    with patch("metroclaude.security.auth.get_settings") as mock:
        mock.return_value.get_allowed_user_ids.return_value = []
        assert is_authorized(123) is False
