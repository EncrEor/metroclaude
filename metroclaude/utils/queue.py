"""Message queue with MessageTask architecture, tool_use/tool_result editing, and status lifecycle.

Resolves:
  - P0-3: Task types (CONTENT, TOOL_USE, TOOL_RESULT, STATUS, STATUS_CLEAR)
  - P0-4: tool_use -> edit -> tool_result pattern (no spam)
  - P1-Q1: Status message lifecycle (create -> edit -> delete)
  - P1-Q2: Merge guards (only CONTENT tasks are mergeable)

Inspired by ccbot's message_queue.py architecture.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Awaitable

from ..config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class TaskType(str, Enum):
    CONTENT = "content"            # Regular text (assistant response) — mergeable
    TOOL_USE = "tool_use"          # Tool invocation -> creates status message
    TOOL_RESULT = "tool_result"    # Tool result -> edits the tool_use message
    STATUS = "status"              # Ephemeral status (e.g. "Claude is thinking...")
    STATUS_CLEAR = "status_clear"  # Clear status message


@dataclass
class MessageTask:
    """A unit of work for the message queue."""
    chat_id: int
    thread_id: int | None
    text: str
    task_type: TaskType = TaskType.CONTENT
    tool_id: str = ""              # For pairing tool_use <-> tool_result


# Type aliases for the callback functions
SendFn = Callable[[int, str, int | None], Awaitable[int | None]]
EditFn = Callable[[int, int, str, int | None], Awaitable[None]]
DeleteFn = Callable[[int, int, int | None], Awaitable[None]]


# ---------------------------------------------------------------------------
# MessageQueue
# ---------------------------------------------------------------------------

class MessageQueue:
    """Per-chat message queue with task-type routing, tool editing, and rate limiting.

    Constructor takes three async callables:
        send_fn(chat_id, text, thread_id) -> message_id | None
        edit_fn(chat_id, message_id, text, thread_id) -> None
        delete_fn(chat_id, message_id, thread_id) -> None
    """

    def __init__(
        self,
        send_fn: SendFn,
        edit_fn: EditFn,
        delete_fn: DeleteFn,
    ) -> None:
        self._send_fn = send_fn
        self._edit_fn = edit_fn
        self._delete_fn = delete_fn
        self._settings = get_settings()

        # Per-chat FIFO queues and workers
        self._queues: dict[str, list[MessageTask]] = defaultdict(list)
        self._workers: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

        # tool_id -> message_id (for editing tool_use messages with results)
        self._tool_msg_ids: dict[str, int] = {}

        # chat_key -> message_id (for status message lifecycle)
        self._status_msg_ids: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _key(self, chat_id: int, thread_id: int | None) -> str:
        return f"{chat_id}:{thread_id or 0}"

    async def enqueue(self, task: MessageTask) -> None:
        """Add a MessageTask to the queue. Starts a worker if none exists."""
        key = self._key(task.chat_id, task.thread_id)

        async with self._lock:
            self._queues[key].append(task)
            if key not in self._workers or self._workers[key].done():
                self._workers[key] = asyncio.create_task(self._worker(key))

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    async def _worker(self, key: str) -> None:
        """Process queued messages for a specific chat, routing by task type."""
        merge_delay = self._settings.message_merge_delay
        max_merge = self._settings.message_merge_max_length

        while True:
            # Wait briefly to allow message merging
            await asyncio.sleep(merge_delay)

            async with self._lock:
                queue = self._queues.get(key, [])
                if not queue:
                    self._queues.pop(key, None)
                    return

                # Pop the first task
                task = queue[0]

                # For CONTENT tasks, try to merge consecutive CONTENT tasks
                if task.task_type == TaskType.CONTENT:
                    merged_text = task.text
                    consumed = 1
                    for candidate in queue[1:]:
                        if candidate.task_type != TaskType.CONTENT:
                            break  # Merge guard: never merge across types
                        combined = merged_text + "\n" + candidate.text
                        if len(combined) > max_merge and merged_text:
                            break
                        merged_text = combined
                        consumed += 1

                    # Remove consumed tasks
                    self._queues[key] = queue[consumed:]
                    # Use merged text
                    task = MessageTask(
                        chat_id=task.chat_id,
                        thread_id=task.thread_id,
                        text=merged_text,
                        task_type=TaskType.CONTENT,
                    )
                else:
                    # Non-mergeable: pop just the one task
                    self._queues[key] = queue[1:]

            # Dispatch by task type
            try:
                if task.task_type == TaskType.CONTENT:
                    await self._process_content(task)
                elif task.task_type == TaskType.TOOL_USE:
                    await self._process_tool_use(task)
                elif task.task_type == TaskType.TOOL_RESULT:
                    await self._process_tool_result(task)
                elif task.task_type == TaskType.STATUS:
                    await self._process_status(task)
                elif task.task_type == TaskType.STATUS_CLEAR:
                    await self._process_status_clear(task)
            except Exception as e:
                retry_after = getattr(e, "retry_after", None)
                if retry_after:
                    logger.warning("Rate limited, retrying in %ds", retry_after)
                    await asyncio.sleep(retry_after)
                else:
                    logger.error("Error processing %s task: %s", task.task_type, e)

    # ------------------------------------------------------------------
    # Task processors
    # ------------------------------------------------------------------

    async def _process_content(self, task: MessageTask) -> None:
        """Send a regular text message. Split if over Telegram limit."""
        chunks = self._split_message(task.text)
        for chunk in chunks:
            msg_id = await self._send_with_retry(task.chat_id, chunk, task.thread_id)
            logger.info("Sent CONTENT → chat %d (msg_id=%s, %d chars)", task.chat_id, msg_id, len(chunk))

    async def _process_tool_use(self, task: MessageTask) -> None:
        """Send tool_use as a new message and store message_id for later editing."""
        msg_id = await self._send_with_retry(task.chat_id, task.text, task.thread_id)
        if msg_id and task.tool_id:
            self._tool_msg_ids[task.tool_id] = msg_id
        logger.info("Sent TOOL_USE → chat %d (msg_id=%s, tool=%s)", task.chat_id, msg_id, task.tool_id[:8] if task.tool_id else "?")

    async def _process_tool_result(self, task: MessageTask) -> None:
        """Edit the matching tool_use message with the result, or send new on error."""
        if not task.tool_id:
            # No tool_id, can't match — send as new message
            await self._send_with_retry(task.chat_id, task.text, task.thread_id)
            return

        msg_id = self._tool_msg_ids.pop(task.tool_id, None)
        if msg_id is None:
            # No matching tool_use message found — skip silently
            logger.debug("No tool_use message for tool_id=%s, skipping result", task.tool_id)
            return

        # Edit the tool_use message to show the result
        try:
            await self._edit_fn(task.chat_id, msg_id, task.text, task.thread_id)
            logger.info("Edited TOOL_RESULT → chat %d (msg_id=%d, tool=%s)", task.chat_id, msg_id, task.tool_id[:8] if task.tool_id else "?")
        except Exception as e:
            logger.warning("Failed to edit tool message %d: %s — sending new", msg_id, e)
            # Fallback: send as new message
            await self._send_with_retry(task.chat_id, task.text, task.thread_id)

    async def _process_status(self, task: MessageTask) -> None:
        """Send or update a status message (one per chat)."""
        key = self._key(task.chat_id, task.thread_id)
        existing_id = self._status_msg_ids.get(key)

        if existing_id:
            # Edit existing status message
            try:
                await self._edit_fn(task.chat_id, existing_id, task.text, task.thread_id)
                return
            except Exception:
                # Edit failed (message deleted or too old) — send new
                self._status_msg_ids.pop(key, None)

        # Send new status message
        msg_id = await self._send_with_retry(task.chat_id, task.text, task.thread_id)
        if msg_id:
            self._status_msg_ids[key] = msg_id

    async def _process_status_clear(self, task: MessageTask) -> None:
        """Delete the current status message for this chat."""
        key = self._key(task.chat_id, task.thread_id)
        msg_id = self._status_msg_ids.pop(key, None)
        if msg_id:
            try:
                await self._delete_fn(task.chat_id, msg_id, task.thread_id)
            except Exception as e:
                logger.debug("Failed to delete status message %d: %s", msg_id, e)

    # ------------------------------------------------------------------
    # Sending helpers
    # ------------------------------------------------------------------

    async def _send_with_retry(
        self,
        chat_id: int,
        text: str,
        thread_id: int | None,
        max_retries: int = 3,
    ) -> int | None:
        """Send with exponential backoff on rate limit. Returns message_id."""
        for attempt in range(max_retries):
            try:
                return await self._send_fn(chat_id, text, thread_id)
            except Exception as e:
                retry_after = getattr(e, "retry_after", None)
                if retry_after:
                    logger.warning("Rate limited, retrying in %ds", retry_after)
                    await asyncio.sleep(retry_after)
                elif attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "Send error (attempt %d), retrying in %ds: %s",
                        attempt + 1, wait, e,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("Failed to send after %d attempts: %s", max_retries, e)
        return None

    # ------------------------------------------------------------------
    # Message splitting (unchanged from original)
    # ------------------------------------------------------------------

    def _split_message(self, text: str) -> list[str]:
        """Split text into chunks that fit within Telegram's message limit.

        Split priority: newline > space > hard cut.
        """
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

    # ------------------------------------------------------------------
    # Cleanup helpers
    # ------------------------------------------------------------------

    def clear_tool_messages(self, tool_id: str | None = None) -> None:
        """Clear tracked tool message IDs.

        If tool_id is given, clear that specific one.
        Otherwise clear all.
        """
        if tool_id:
            self._tool_msg_ids.pop(tool_id, None)
        else:
            self._tool_msg_ids.clear()

    def clear_status_message(self, chat_id: int, thread_id: int | None = None) -> None:
        """Clear status message tracking for a specific chat."""
        key = self._key(chat_id, thread_id)
        self._status_msg_ids.pop(key, None)
