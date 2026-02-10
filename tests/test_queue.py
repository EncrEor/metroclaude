"""Tests for the message queue."""

from metroclaude.utils.queue import MessageQueue


def test_split_short_message():
    """Messages under 4096 chars should not be split."""
    async def mock_send(chat_id, text, thread_id):
        pass

    q = MessageQueue(mock_send)
    chunks = q._split_message("Hello world")
    assert len(chunks) == 1
    assert chunks[0] == "Hello world"


def test_split_long_message():
    """Messages over 4096 chars should be split at newlines."""
    async def mock_send(chat_id, text, thread_id):
        pass

    q = MessageQueue(mock_send)
    # Create a message that's too long
    long_msg = "\n".join(f"Line {i}: " + "x" * 80 for i in range(100))
    chunks = q._split_message(long_msg)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 4096
