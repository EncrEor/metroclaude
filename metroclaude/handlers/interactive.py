"""Interactive UI handlers — keyboard builders, tracker, and formatters.

Builds Telegram inline keyboards for Claude Code interactive prompts:
  - Permission prompts (Allow/Deny)
  - AskUserQuestion (multiple choice)
  - ExitPlanMode (Proceed/Cancel)
  - RestoreCheckpoint (Yes/No)
  - Restart/Refresh after Claude exits
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .callback_data import (
    CB_ASKUSER,
    CB_PERMIT_NO,
    CB_PERMIT_YES,
    CB_PLANMODE_NO,
    CB_PLANMODE_YES,
    CB_REFRESH,
    CB_RESTART,
    CB_RESTORE_NO,
    CB_RESTORE_YES,
    encode_callback,
)
from .status import InteractiveUIInfo

logger = logging.getLogger(__name__)

# Patterns for extracting AskUserQuestion options from terminal content
_CHECKBOX_RE = re.compile(r"^\s*[←]?\s*([☐✔☒])\s+(.+)")
_NUMBERED_RE = re.compile(r"^\s*(\d+)[.)]\s+(.+)")


# ------------------------------------------------------------------
# Keyboard builders
# ------------------------------------------------------------------


def build_permission_keyboard(window_name: str) -> InlineKeyboardMarkup:
    """Build a 2-button keyboard for permission prompts (Allow / Deny)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Allow", callback_data=encode_callback(CB_PERMIT_YES, window_name)),
        InlineKeyboardButton("Deny", callback_data=encode_callback(CB_PERMIT_NO, window_name)),
    ]])


def build_askuser_keyboard(content: str, window_name: str) -> InlineKeyboardMarkup:
    """Build a keyboard with options extracted from AskUserQuestion content."""
    options = parse_askuser_options(content)
    if not options:
        # Fallback: single "Reply" button (user types answer)
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("Reply...", callback_data=encode_callback(CB_ASKUSER, window_name, 0)),
        ]])
    buttons = []
    for idx, label in options:
        # Truncate label for button display (max 40 chars)
        display = label[:40] + "..." if len(label) > 40 else label
        buttons.append([InlineKeyboardButton(
            display,
            callback_data=encode_callback(CB_ASKUSER, window_name, idx),
        )])
    return InlineKeyboardMarkup(buttons)


def build_planmode_keyboard(window_name: str) -> InlineKeyboardMarkup:
    """Build a 2-button keyboard for ExitPlanMode (Proceed / Cancel)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Proceed", callback_data=encode_callback(CB_PLANMODE_YES, window_name)),
        InlineKeyboardButton("Cancel", callback_data=encode_callback(CB_PLANMODE_NO, window_name)),
    ]])


def build_restore_keyboard(window_name: str) -> InlineKeyboardMarkup:
    """Build a 2-button keyboard for RestoreCheckpoint (Yes / No)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Restore", callback_data=encode_callback(CB_RESTORE_YES, window_name)),
        InlineKeyboardButton("Keep", callback_data=encode_callback(CB_RESTORE_NO, window_name)),
    ]])


def build_restart_keyboard(window_name: str) -> InlineKeyboardMarkup:
    """Build a 2-button keyboard for post-exit (Restart / Refresh)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Restart", callback_data=encode_callback(CB_RESTART, window_name)),
        InlineKeyboardButton("Refresh", callback_data=encode_callback(CB_REFRESH, window_name)),
    ]])


def build_keyboard_for_ui(ui_info: InteractiveUIInfo, window_name: str) -> InlineKeyboardMarkup | None:
    """Dispatch to the right keyboard builder based on UI type."""
    builders = {
        "PermissionPrompt": lambda: build_permission_keyboard(window_name),
        "AskUserQuestion": lambda: build_askuser_keyboard(ui_info.content, window_name),
        "ExitPlanMode": lambda: build_planmode_keyboard(window_name),
        "RestoreCheckpoint": lambda: build_restore_keyboard(window_name),
    }
    builder = builders.get(ui_info.name)
    if builder:
        return builder()
    return None


# ------------------------------------------------------------------
# AskUserQuestion option parser
# ------------------------------------------------------------------


def parse_askuser_options(content: str) -> list[tuple[int, str]]:
    """Extract options from AskUserQuestion terminal content.

    Looks for lines matching:
      - Checkbox patterns: [arrow] checkbox_char label
      - Numbered patterns: N. label or N) label

    Returns list of (index, label) tuples.
    """
    options: list[tuple[int, str]] = []
    idx = 0
    for line in content.split("\n"):
        m = _CHECKBOX_RE.match(line)
        if m:
            options.append((idx, m.group(2).strip()))
            idx += 1
            continue
        m = _NUMBERED_RE.match(line)
        if m:
            options.append((idx, m.group(2).strip()))
            idx += 1
    return options


# ------------------------------------------------------------------
# Anti-spam tracker
# ------------------------------------------------------------------


class InteractiveTracker:
    """Track which interactive UIs have been sent to avoid duplicates.

    Each window can have at most one active interactive keyboard.
    Dedup is based on (ui_name, content_hash) — if the same UI with
    the same content is detected again, we skip sending.
    """

    def __init__(self) -> None:
        # window_name -> (ui_name, msg_id, content_hash)
        self._active: dict[str, tuple[str, int, str]] = {}

    def should_send(self, window_name: str, ui_name: str, content: str) -> bool:
        """Return True if this UI hasn't been sent yet (or content changed)."""
        h = _content_hash(content)
        existing = self._active.get(window_name)
        if existing is None:
            return True
        old_name, _, old_hash = existing
        if old_name != ui_name or old_hash != h:
            return True
        return False

    def mark_sent(self, window_name: str, ui_name: str, msg_id: int, content: str) -> None:
        """Record that a keyboard was sent."""
        h = _content_hash(content)
        self._active[window_name] = (ui_name, msg_id, h)

    def clear(self, window_name: str) -> None:
        """Clear tracker for a window (after user responds)."""
        self._active.pop(window_name, None)

    def get_msg_id(self, window_name: str) -> int | None:
        """Get the message_id of the active keyboard for editing."""
        existing = self._active.get(window_name)
        if existing:
            return existing[1]
        return None


def _content_hash(content: str) -> str:
    """Short hash of content for dedup comparison."""
    return hashlib.md5(content.encode()).hexdigest()[:12]


# ------------------------------------------------------------------
# Notification text builders
# ------------------------------------------------------------------


def format_permission_text(content: str) -> str:
    """Format a permission prompt notification for Telegram."""
    # Extract the key info — first few meaningful lines
    lines = [l.strip() for l in content.split("\n") if l.strip()]
    preview = "\n".join(lines[:5])
    if len(lines) > 5:
        preview += "\n..."
    return f"**Permission required**\n\n```\n{preview}\n```"


def format_askuser_text(content: str) -> str:
    """Format an AskUserQuestion notification for Telegram."""
    lines = [l.strip() for l in content.split("\n") if l.strip()]
    preview = "\n".join(lines[:8])
    if len(lines) > 8:
        preview += "\n..."
    return f"**Question from Claude**\n\n```\n{preview}\n```"


def format_exit_text(window_name: str) -> str:
    """Format a Claude exit notification."""
    return f"Claude has exited in **{window_name}**"
