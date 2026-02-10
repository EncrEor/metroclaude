"""Tests for callback_data encoding/decoding and interactive UI handlers."""

from metroclaude.handlers.callback_data import (
    CB_ASKUSER,
    CB_PERMIT_NO,
    CB_PERMIT_YES,
    CB_REFRESH,
    CB_RESTART,
    decode_callback,
    encode_callback,
)
from metroclaude.handlers.interactive import (
    InteractiveTracker,
    build_askuser_keyboard,
    build_permission_keyboard,
    build_restart_keyboard,
    parse_askuser_options,
)


# ------------------------------------------------------------------
# callback_data.py tests
# ------------------------------------------------------------------


def test_encode_decode_callback():
    """Roundtrip: encode then decode should return original values."""
    data = encode_callback(CB_PERMIT_YES, "my-window")
    prefix, window, index = decode_callback(data)
    assert prefix == CB_PERMIT_YES
    assert window == "my-window"
    assert index is None


def test_encode_decode_with_index():
    """Roundtrip with index (AskUserQuestion)."""
    data = encode_callback(CB_ASKUSER, "win-abc", index=2)
    prefix, window, index = decode_callback(data)
    assert prefix == CB_ASKUSER
    assert window == "win-abc"
    assert index == 2


def test_encode_truncation():
    """Long window names are truncated to fit 64 byte Telegram limit."""
    long_name = "a" * 100
    data = encode_callback(CB_PERMIT_YES, long_name)
    assert len(data) <= 64
    # Should still decode (prefix intact, window truncated)
    prefix, window, index = decode_callback(data)
    assert prefix == CB_PERMIT_YES
    assert index is None
    assert len(window) > 0


def test_decode_single_part():
    """Decode a callback with no colon separator."""
    prefix, window, index = decode_callback("xy")
    assert prefix == "xy"
    assert window == ""
    assert index is None


# ------------------------------------------------------------------
# interactive.py keyboard tests
# ------------------------------------------------------------------


def test_build_permission_keyboard():
    """Permission keyboard has 2 buttons: Allow and Deny."""
    kb = build_permission_keyboard("test-win")
    buttons = kb.inline_keyboard
    assert len(buttons) == 1  # 1 row
    assert len(buttons[0]) == 2  # 2 buttons
    assert buttons[0][0].text == "Allow"
    assert buttons[0][1].text == "Deny"
    # Check callback data format
    assert buttons[0][0].callback_data.startswith(CB_PERMIT_YES + ":")
    assert buttons[0][1].callback_data.startswith(CB_PERMIT_NO + ":")


def test_build_askuser_keyboard():
    """AskUser keyboard parses options from terminal content."""
    content = (
        "  Which file do you want to edit?\n"
        "  ☐ src/main.py\n"
        "  ☐ src/utils.py\n"
        "  ☐ tests/test_main.py\n"
    )
    kb = build_askuser_keyboard(content, "win-123")
    buttons = kb.inline_keyboard
    assert len(buttons) == 3  # 3 options = 3 rows
    assert "src/main.py" in buttons[0][0].text
    assert "src/utils.py" in buttons[1][0].text
    assert "tests/test_main.py" in buttons[2][0].text


def test_build_askuser_keyboard_no_options():
    """AskUser with no parseable options gives a fallback button."""
    kb = build_askuser_keyboard("Some question without options", "win-x")
    buttons = kb.inline_keyboard
    assert len(buttons) == 1
    assert buttons[0][0].text == "Reply..."


def test_build_restart_keyboard():
    """Restart keyboard has 2 buttons: Restart and Refresh."""
    kb = build_restart_keyboard("win-done")
    buttons = kb.inline_keyboard
    assert len(buttons) == 1  # 1 row
    assert len(buttons[0]) == 2  # 2 buttons
    assert buttons[0][0].text == "Restart"
    assert buttons[0][1].text == "Refresh"
    assert buttons[0][0].callback_data.startswith(CB_RESTART + ":")
    assert buttons[0][1].callback_data.startswith(CB_REFRESH + ":")


# ------------------------------------------------------------------
# parse_askuser_options tests
# ------------------------------------------------------------------


def test_parse_checkbox_options():
    """Parse checkbox-style options."""
    content = "← ☐ Option A\n  ☐ Option B\n  ✔ Option C"
    options = parse_askuser_options(content)
    assert len(options) == 3
    assert options[0] == (0, "Option A")
    assert options[1] == (1, "Option B")
    assert options[2] == (2, "Option C")


def test_parse_numbered_options():
    """Parse numbered-style options."""
    content = "Choose:\n1. First choice\n2. Second choice\n3. Third choice"
    options = parse_askuser_options(content)
    assert len(options) == 3
    assert options[0] == (0, "First choice")
    assert options[1] == (1, "Second choice")
    assert options[2] == (2, "Third choice")


def test_parse_no_options():
    """No parseable options returns empty list."""
    assert parse_askuser_options("Just a question, no options") == []


# ------------------------------------------------------------------
# InteractiveTracker tests
# ------------------------------------------------------------------


def test_interactive_tracker_dedup():
    """should_send=True first time, False for same content."""
    tracker = InteractiveTracker()
    assert tracker.should_send("win-1", "PermissionPrompt", "Allow access?") is True
    tracker.mark_sent("win-1", "PermissionPrompt", 42, "Allow access?")
    assert tracker.should_send("win-1", "PermissionPrompt", "Allow access?") is False


def test_interactive_tracker_content_change():
    """should_send=True if content changes for same window."""
    tracker = InteractiveTracker()
    tracker.mark_sent("win-1", "PermissionPrompt", 42, "Allow read?")
    assert tracker.should_send("win-1", "PermissionPrompt", "Allow write?") is True


def test_interactive_tracker_ui_name_change():
    """should_send=True if UI type changes for same window."""
    tracker = InteractiveTracker()
    tracker.mark_sent("win-1", "PermissionPrompt", 42, "content")
    assert tracker.should_send("win-1", "ExitPlanMode", "content") is True


def test_interactive_tracker_clear():
    """should_send=True after clear (user responded)."""
    tracker = InteractiveTracker()
    tracker.mark_sent("win-1", "PermissionPrompt", 42, "Allow?")
    assert tracker.should_send("win-1", "PermissionPrompt", "Allow?") is False
    tracker.clear("win-1")
    assert tracker.should_send("win-1", "PermissionPrompt", "Allow?") is True


def test_interactive_tracker_get_msg_id():
    """get_msg_id returns the message_id of the active keyboard."""
    tracker = InteractiveTracker()
    assert tracker.get_msg_id("win-1") is None
    tracker.mark_sent("win-1", "PermissionPrompt", 99, "content")
    assert tracker.get_msg_id("win-1") == 99
    tracker.clear("win-1")
    assert tracker.get_msg_id("win-1") is None
