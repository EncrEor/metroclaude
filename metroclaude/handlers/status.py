"""Status handler — typing indicators and spinner detection."""

from __future__ import annotations

import asyncio
import logging
import re

from telegram import Bot

from ..config import get_settings

logger = logging.getLogger(__name__)

# Spinner characters used by Claude Code CLI
SPINNER_CHARS = {"·", "✻", "✽", "✶", "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}


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
    """Detect if Claude is waiting for input (showing prompt)."""
    if not terminal_content:
        return False
    last_lines = terminal_content.strip().split("\n")[-3:]
    for line in last_lines:
        stripped = line.strip()
        # Claude prompt looks like: > or claude>
        if stripped == ">" or stripped.endswith(">") and len(stripped) < 20:
            return True
    return False
