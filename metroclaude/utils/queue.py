"""Message queue with auto-merge and Telegram rate-limit handling.

Messages sent within a short window are merged into one (reduces spam).
Messages exceeding Telegram's 4096 char limit are split.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field

from ..config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class PendingMessage:
    chat_id: int
    thread_id: int | None
    text: str


class MessageQueue:
    """Per-chat message queue with auto-merge and rate limiting."""

    def __init__(self, send_fn) -> None:  # noqa: ANN001
        """
        Args:
            send_fn: async callable(chat_id, text, thread_id) that sends to Telegram.
        """
        self._send_fn = send_fn
        self._settings = get_settings()
        self._queues: dict[str, list[PendingMessage]] = defaultdict(list)
        self._workers: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def _key(self, chat_id: int, thread_id: int | None) -> str:
        return f"{chat_id}:{thread_id or 0}"

    async def enqueue(self, chat_id: int, text: str, thread_id: int | None = None) -> None:
        """Add a message to the queue. Starts a worker if none exists."""
        key = self._key(chat_id, thread_id)
        msg = PendingMessage(chat_id=chat_id, thread_id=thread_id, text=text)

        async with self._lock:
            self._queues[key].append(msg)
            if key not in self._workers or self._workers[key].done():
                self._workers[key] = asyncio.create_task(self._worker(key))

    async def _worker(self, key: str) -> None:
        """Process queued messages for a specific chat, merging where possible."""
        merge_delay = self._settings.message_merge_delay
        max_len = self._settings.message_merge_max_length

        while True:
            # Wait briefly to allow message merging
            await asyncio.sleep(merge_delay)

            async with self._lock:
                queue = self._queues.get(key, [])
                if not queue:
                    self._queues.pop(key, None)
                    return

                # Merge consecutive messages
                merged_text = ""
                consumed = 0
                for msg in queue:
                    candidate = (merged_text + "\n" + msg.text).strip() if merged_text else msg.text
                    if len(candidate) > max_len and merged_text:
                        break
                    merged_text = candidate
                    consumed += 1

                messages_to_send = queue[:consumed]
                self._queues[key] = queue[consumed:]

            if not messages_to_send:
                continue

            ref = messages_to_send[0]

            # Split if still over Telegram limit
            for chunk in self._split_message(merged_text):
                await self._send_with_retry(ref.chat_id, chunk, ref.thread_id)

    async def _send_with_retry(
        self, chat_id: int, text: str, thread_id: int | None, max_retries: int = 3,
    ) -> None:
        """Send with exponential backoff on rate limit."""
        for attempt in range(max_retries):
            try:
                await self._send_fn(chat_id, text, thread_id)
                return
            except Exception as e:
                # Check for Telegram RetryAfter
                retry_after = getattr(e, "retry_after", None)
                if retry_after:
                    logger.warning("Rate limited, retrying in %ds", retry_after)
                    await asyncio.sleep(retry_after)
                elif attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning("Send error (attempt %d), retrying in %ds: %s", attempt + 1, wait, e)
                    await asyncio.sleep(wait)
                else:
                    logger.error("Failed to send after %d attempts: %s", max_retries, e)

    def _split_message(self, text: str) -> list[str]:
        """Split text into chunks that fit within Telegram's message limit."""
        max_len = self._settings.telegram_max_message_length
        if len(text) <= max_len:
            return [text]

        chunks: list[str] = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break

            # Try to split at a newline
            split_pos = text.rfind("\n", 0, max_len)
            if split_pos == -1 or split_pos < max_len // 2:
                # Try space
                split_pos = text.rfind(" ", 0, max_len)
            if split_pos == -1 or split_pos < max_len // 2:
                split_pos = max_len

            chunks.append(text[:split_pos])
            text = text[split_pos:].lstrip("\n")

        return chunks
