"""Tests for the message queue."""

import asyncio

import pytest

from metroclaude.utils.queue import MessageQueue, MessageTask, TaskType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_queue(
    sent: list | None = None,
    edited: list | None = None,
    deleted: list | None = None,
) -> MessageQueue:
    """Create a MessageQueue with mock send/edit/delete functions.

    Each mock appends its call args to the provided list for assertions.
    send_fn returns an auto-incrementing message_id.
    """
    _counter = {"n": 0}

    async def mock_send(chat_id, text, thread_id):
        _counter["n"] += 1
        msg_id = _counter["n"]
        if sent is not None:
            sent.append({"chat_id": chat_id, "text": text, "thread_id": thread_id, "msg_id": msg_id})
        return msg_id

    async def mock_edit(chat_id, message_id, text, thread_id):
        if edited is not None:
            edited.append({"chat_id": chat_id, "message_id": message_id, "text": text, "thread_id": thread_id})

    async def mock_delete(chat_id, message_id, thread_id):
        if deleted is not None:
            deleted.append({"chat_id": chat_id, "message_id": message_id, "thread_id": thread_id})

    return MessageQueue(mock_send, mock_edit, mock_delete)


# ---------------------------------------------------------------------------
# _split_message tests (preserved from original)
# ---------------------------------------------------------------------------

def test_split_short_message():
    """Messages under 4096 chars should not be split."""
    q = _make_queue()
    chunks = q._split_message("Hello world")
    assert len(chunks) == 1
    assert chunks[0] == "Hello world"


def test_split_long_message():
    """Messages over 4096 chars should be split at newlines."""
    q = _make_queue()
    # Create a message that's too long
    long_msg = "\n".join(f"Line {i}: " + "x" * 80 for i in range(100))
    chunks = q._split_message(long_msg)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 4096


# ---------------------------------------------------------------------------
# TaskType and MessageTask
# ---------------------------------------------------------------------------

def test_task_type_values():
    """TaskType enum has the expected string values."""
    assert TaskType.CONTENT == "content"
    assert TaskType.TOOL_USE == "tool_use"
    assert TaskType.TOOL_RESULT == "tool_result"
    assert TaskType.STATUS == "status"
    assert TaskType.STATUS_CLEAR == "status_clear"


def test_message_task_defaults():
    """MessageTask defaults to CONTENT with empty tool_id."""
    task = MessageTask(chat_id=1, thread_id=None, text="hello")
    assert task.task_type == TaskType.CONTENT
    assert task.tool_id == ""


# ---------------------------------------------------------------------------
# Enqueue + worker integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_content_sends_message():
    """A CONTENT task should send via send_fn."""
    sent = []
    q = _make_queue(sent=sent)

    await q.enqueue(MessageTask(chat_id=100, thread_id=42, text="Hello"))
    # Give the worker time to process
    await asyncio.sleep(0.8)

    assert len(sent) == 1
    assert sent[0]["chat_id"] == 100
    assert sent[0]["text"] == "Hello"
    assert sent[0]["thread_id"] == 42


@pytest.mark.asyncio
async def test_content_merge():
    """Consecutive CONTENT tasks within merge window should be merged."""
    sent = []
    q = _make_queue(sent=sent)

    # Enqueue two CONTENT tasks rapidly (within merge delay)
    await q.enqueue(MessageTask(chat_id=100, thread_id=None, text="Line 1"))
    await q.enqueue(MessageTask(chat_id=100, thread_id=None, text="Line 2"))
    await asyncio.sleep(0.8)

    assert len(sent) == 1
    assert "Line 1" in sent[0]["text"]
    assert "Line 2" in sent[0]["text"]


@pytest.mark.asyncio
async def test_tool_use_not_merged_with_content():
    """TOOL_USE should NOT be merged with preceding CONTENT."""
    sent = []
    q = _make_queue(sent=sent)

    await q.enqueue(MessageTask(chat_id=100, thread_id=None, text="Some text"))
    await q.enqueue(MessageTask(
        chat_id=100, thread_id=None, text="tool msg",
        task_type=TaskType.TOOL_USE, tool_id="t1",
    ))
    await asyncio.sleep(1.5)  # Wait for both to process

    assert len(sent) == 2
    assert sent[0]["text"] == "Some text"
    assert sent[1]["text"] == "tool msg"


@pytest.mark.asyncio
async def test_tool_result_edits_tool_use():
    """TOOL_RESULT should edit the TOOL_USE message, not send new."""
    sent = []
    edited = []
    q = _make_queue(sent=sent, edited=edited)

    # Send tool_use first
    await q.enqueue(MessageTask(
        chat_id=100, thread_id=None, text="Running tool...",
        task_type=TaskType.TOOL_USE, tool_id="t1",
    ))
    await asyncio.sleep(0.8)

    # Now send tool_result
    await q.enqueue(MessageTask(
        chat_id=100, thread_id=None, text="Tool done!",
        task_type=TaskType.TOOL_RESULT, tool_id="t1",
    ))
    await asyncio.sleep(0.8)

    # tool_use was sent, tool_result was edited
    assert len(sent) == 1  # Only tool_use sent
    assert len(edited) == 1
    assert edited[0]["message_id"] == sent[0]["msg_id"]
    assert edited[0]["text"] == "Tool done!"


@pytest.mark.asyncio
async def test_tool_result_no_match_skips():
    """TOOL_RESULT with no matching tool_use should be skipped."""
    sent = []
    edited = []
    q = _make_queue(sent=sent, edited=edited)

    await q.enqueue(MessageTask(
        chat_id=100, thread_id=None, text="orphan result",
        task_type=TaskType.TOOL_RESULT, tool_id="no_match",
    ))
    await asyncio.sleep(0.8)

    assert len(sent) == 0
    assert len(edited) == 0


@pytest.mark.asyncio
async def test_status_lifecycle():
    """STATUS -> STATUS -> STATUS_CLEAR: create, edit, delete."""
    sent = []
    edited = []
    deleted = []
    q = _make_queue(sent=sent, edited=edited, deleted=deleted)

    # First status: creates a new message
    await q.enqueue(MessageTask(
        chat_id=100, thread_id=None, text="Thinking...",
        task_type=TaskType.STATUS,
    ))
    await asyncio.sleep(0.8)
    assert len(sent) == 1

    # Second status: edits the existing one
    await q.enqueue(MessageTask(
        chat_id=100, thread_id=None, text="Still thinking...",
        task_type=TaskType.STATUS,
    ))
    await asyncio.sleep(0.8)
    assert len(edited) == 1
    assert edited[0]["message_id"] == sent[0]["msg_id"]

    # Clear: deletes the status message
    await q.enqueue(MessageTask(
        chat_id=100, thread_id=None, text="",
        task_type=TaskType.STATUS_CLEAR,
    ))
    await asyncio.sleep(0.8)
    assert len(deleted) == 1
    assert deleted[0]["message_id"] == sent[0]["msg_id"]


@pytest.mark.asyncio
async def test_merge_guard_tool_use_breaks_merge():
    """A TOOL_USE in the queue should stop CONTENT merging."""
    sent = []
    q = _make_queue(sent=sent)

    await q.enqueue(MessageTask(chat_id=100, thread_id=None, text="A"))
    await q.enqueue(MessageTask(chat_id=100, thread_id=None, text="B"))
    await q.enqueue(MessageTask(
        chat_id=100, thread_id=None, text="tool",
        task_type=TaskType.TOOL_USE, tool_id="t1",
    ))
    await q.enqueue(MessageTask(chat_id=100, thread_id=None, text="C"))
    await asyncio.sleep(2.0)

    # A+B merged, tool separate, C separate
    assert len(sent) == 3
    assert "A" in sent[0]["text"] and "B" in sent[0]["text"]
    assert sent[1]["text"] == "tool"
    assert sent[2]["text"] == "C"
