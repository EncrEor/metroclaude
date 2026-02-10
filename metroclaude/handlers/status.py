"""Status handler — typing indicators, spinner/prompt/exit detection.

Detects Claude Code CLI state from terminal output:
  - Spinner (Claude is working)
  - Prompt (Claude is waiting for user input)
  - Interactive UI (permission prompt, plan mode, etc.)
  - Exit (Claude process has ended, shell returned)
  - Status line text (spinner + description)

References:
  - ccbot terminal_parser.py (UIPattern, parse_status_line)
  - ccbot status_polling.py (exit detection via pane_current_command)
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from telegram import Bot

from ..config import get_settings

logger = logging.getLogger(__name__)

# Spinner characters used by Claude Code CLI
# Includes both standard Unicode spinners and braille animation chars
SPINNER_CHARS = {"·", "✻", "✽", "✶", "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}

# Spinner chars used in status line (subset — no braille, these are text spinners)
STATUS_SPINNERS = frozenset({"·", "✻", "✽", "✶", "✳", "✢"})

# Prompt patterns — Claude Code shows these when waiting for input
_PROMPT_RE = re.compile(
    r"^(?:"
    r">"                   # bare ">"
    r"|claude\s*>"         # "claude>" or "claude >"
    r"|[a-zA-Z0-9._-]+>"  # "project-name>" style prompts
    r")$"
)

# Shell prompts — if we see these instead of Claude, Claude has exited
_SHELL_COMMANDS = frozenset({"bash", "zsh", "sh", "fish", "dash", "ksh", "tcsh"})

# Interactive UI top markers (simplified from ccbot UIPattern)
_INTERACTIVE_UI_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ExitPlanMode", re.compile(r"^\s*Would you like to proceed\?")),
    ("ExitPlanMode", re.compile(r"^\s*Claude has written up a plan")),
    ("PermissionPrompt", re.compile(r"^\s*Do you want to proceed\?")),
    ("AskUserQuestion", re.compile(r"^\s*[←]?\s*[☐✔☒]")),
    ("RestoreCheckpoint", re.compile(r"^\s*Restore the code")),
]

# Interactive UI bottom markers (confirmation that an interactive UI is active)
_INTERACTIVE_UI_BOTTOM: list[re.Pattern[str]] = [
    re.compile(r"^\s*ctrl-g to edit"),
    re.compile(r"^\s*Esc to (cancel|exit)"),
    re.compile(r"^\s*Enter to (select|continue)"),
    re.compile(r"^\s*Allow|Deny"),
    re.compile(r"^\s*Yes|No"),
]


@dataclass
class InteractiveUIInfo:
    """Detected interactive UI in terminal output."""
    name: str       # Pattern name: "PermissionPrompt", "ExitPlanMode", etc.
    content: str    # Extracted text between top and bottom markers


class TypingManager:
    """Manage Telegram typing indicator for active sessions."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self._active: dict[str, asyncio.Task] = {}  # key = "chatid:topicid"

    def start_typing(self, chat_id: int, topic_id: int | None = None) -> None:
        """Start showing typing indicator for a chat/topic."""
        key = f"{chat_id}:{topic_id or 0}"
        if key in self._active and not self._active[key].done():
            return
        self._active[key] = asyncio.create_task(
            self._typing_loop(chat_id, topic_id)
        )

    def stop_typing(self, chat_id: int, topic_id: int | None = None) -> None:
        """Stop showing typing indicator."""
        key = f"{chat_id}:{topic_id or 0}"
        task = self._active.pop(key, None)
        if task and not task.done():
            task.cancel()

    async def _typing_loop(self, chat_id: int, topic_id: int | None) -> None:
        """Send typing action every 4 seconds (Telegram shows it for ~5s)."""
        try:
            while True:
                kwargs = {"chat_id": chat_id, "action": "typing"}
                if topic_id:
                    kwargs["message_thread_id"] = topic_id
                try:
                    await self._bot.send_chat_action(**kwargs)
                except Exception as e:
                    logger.debug("Typing action error: %s", e)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    def stop_all(self) -> None:
        """Stop all typing indicators."""
        for task in self._active.values():
            if not task.done():
                task.cancel()
        self._active.clear()


def detect_spinner(terminal_content: str) -> bool:
    """Detect if Claude is working by checking for spinner characters in terminal."""
    if not terminal_content:
        return False
    # Check last few lines for spinner chars
    last_lines = terminal_content.strip().split("\n")[-3:]
    for line in last_lines:
        for char in SPINNER_CHARS:
            if char in line:
                return True
    return False


def detect_claude_prompt(terminal_content: str) -> bool:
    """Detect if Claude is waiting for user input (showing prompt).

    Claude Code prompt patterns:
      - ">"           bare prompt
      - "claude>"     named prompt
      - "name>"       project-name style prompt (short, ends with >)
    """
    if not terminal_content:
        return False
    last_lines = terminal_content.strip().split("\n")[-3:]
    for line in last_lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Exact match for bare prompt
        if stripped == ">":
            return True
        # Short string ending with ">" — e.g. "claude>", "project>"
        # Parentheses fix P1-SEC11: explicit grouping for clarity
        if (stripped.endswith(">") and len(stripped) < 20):
            # Exclude lines that look like HTML tags or shell redirects
            if not stripped.startswith("<") and ">>" not in stripped:
                return True
        # Regex match for known prompt formats
        if _PROMPT_RE.match(stripped):
            return True
    return False


def detect_interactive_ui(terminal_content: str) -> InteractiveUIInfo | None:
    """Detect if Claude is showing an interactive UI (permission, plan, etc.).

    Scans terminal output for known interactive UI markers from Claude Code.
    Returns info about the detected UI, or None if no interactive UI found.

    Based on ccbot's UIPattern approach: match top markers, then confirm
    with bottom markers in subsequent lines.
    """
    if not terminal_content:
        return None

    lines = terminal_content.strip().split("\n")

    # Scan for top markers
    for i, line in enumerate(lines):
        for name, pattern in _INTERACTIVE_UI_PATTERNS:
            if pattern.search(line):
                # Found a top marker — check remaining lines for bottom markers
                remaining = lines[i + 1:]
                has_bottom = False
                for rline in remaining:
                    if any(bp.search(rline) for bp in _INTERACTIVE_UI_BOTTOM):
                        has_bottom = True
                        break

                # For AskUserQuestion, bottom marker is optional (multi-tab)
                if has_bottom or name == "AskUserQuestion":
                    content = "\n".join(lines[i:]).rstrip()
                    return InteractiveUIInfo(name=name, content=content)

    return None


def detect_claude_exit(pane_current_command: str | None) -> bool:
    """Detect if Claude Code has exited by checking the pane's current command.

    When Claude is running, `pane_current_command` is "claude" (or "node").
    When Claude exits, the shell resumes and it becomes "bash", "zsh", etc.

    This is the primary mechanism for exit detection (P0-9 from audit).

    Args:
        pane_current_command: The tmux pane's current foreground command name.
            Obtained via libtmux `pane.pane_current_command`.

    Returns:
        True if the command indicates Claude has exited (shell is active).
        False if Claude appears to still be running, or if command is None.
    """
    if not pane_current_command:
        return False
    cmd = pane_current_command.strip().lower()
    # Claude process names: "claude", "node" (Claude runs on Node.js)
    if cmd in ("claude", "node"):
        return False
    # If pane command is a known shell, Claude has exited
    if cmd in _SHELL_COMMANDS:
        return True
    # Unknown command — could be a subprocess, don't flag as exit
    return False


def parse_status_line(terminal_content: str) -> str | None:
    """Extract the Claude Code status line text from terminal output.

    Status lines start with a spinner character (STATUS_SPINNERS).
    Scans from bottom up since the status line is near the bottom.

    Returns the text after the spinner, or None if no status line found.

    Example: "· Reading file.py" -> "Reading file.py"
    """
    if not terminal_content:
        return None

    lines = terminal_content.strip().split("\n")
    # Search bottom 15 lines, reversed (status line is near bottom)
    for line in reversed(lines[-15:]):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0] in STATUS_SPINNERS:
            return stripped[1:].strip()
    return None
