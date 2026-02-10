"""Tests for the JSONL transcript parser."""

import json

from metroclaude.parser import EventType, format_event_for_telegram, parse_jsonl_line


def _make_assistant_line(*content_blocks):
    return json.dumps({
        "type": "assistant",
        "message": {"content": list(content_blocks)},
        "uuid": "test-uuid",
        "timestamp": "2026-02-10T00:00:00Z",
    })


def test_parse_text():
    line = _make_assistant_line({"type": "text", "text": "Hello world"})
    events = parse_jsonl_line(line)
    assert len(events) == 1
    assert events[0].event_type == EventType.TEXT
    assert events[0].content == "Hello world"


def test_parse_tool_use():
    line = _make_assistant_line({
        "type": "tool_use",
        "id": "toolu_123",
        "name": "Bash",
        "input": {"command": "ls -la"},
    })
    events = parse_jsonl_line(line)
    assert len(events) == 1
    assert events[0].event_type == EventType.TOOL_USE
    assert events[0].tool_name == "Bash"
    assert events[0].tool_input_summary == "ls -la"


def test_parse_multiple_blocks():
    line = _make_assistant_line(
        {"type": "thinking", "thinking": "Let me think..."},
        {"type": "text", "text": "Here's the answer"},
        {"type": "tool_use", "id": "toolu_456", "name": "Read", "input": {"file_path": "/tmp/test.py"}},
    )
    events = parse_jsonl_line(line)
    # thinking is skipped, text + tool_use = 2
    assert len(events) == 2
    assert events[0].event_type == EventType.TEXT
    assert events[1].event_type == EventType.TOOL_USE


def test_parse_empty_line():
    assert parse_jsonl_line("") == []
    assert parse_jsonl_line("  ") == []


def test_parse_invalid_json():
    assert parse_jsonl_line("{invalid") == []


def test_parse_tool_result():
    line = json.dumps({
        "type": "user",
        "message": {
            "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_789",
                "content": "file contents here",
                "is_error": False,
            }],
        },
        "uuid": "test-uuid",
        "timestamp": "2026-02-10T00:00:00Z",
    })
    events = parse_jsonl_line(line)
    assert len(events) == 1
    assert events[0].event_type == EventType.TOOL_RESULT
    assert events[0].tool_id == "toolu_789"


def test_format_text_event():
    line = _make_assistant_line({"type": "text", "text": "Hello!"})
    events = parse_jsonl_line(line)
    fmt = format_event_for_telegram(events[0])
    assert fmt == "Hello!"


def test_format_tool_event():
    line = _make_assistant_line({
        "type": "tool_use",
        "id": "toolu_abc",
        "name": "Grep",
        "input": {"pattern": "def main"},
    })
    events = parse_jsonl_line(line)
    fmt = format_event_for_telegram(events[0])
    assert "Grep" in fmt
    assert "def main" in fmt


def test_format_tool_result_hidden():
    """Tool results should not be displayed (avoid spam)."""
    line = json.dumps({
        "type": "user",
        "message": {
            "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_xxx",
                "content": "some result",
                "is_error": False,
            }],
        },
        "uuid": "test", "timestamp": "",
    })
    events = parse_jsonl_line(line)
    fmt = format_event_for_telegram(events[0])
    assert fmt is None  # Hidden


def test_format_error_result():
    """Error tool results SHOULD be displayed."""
    line = json.dumps({
        "type": "user",
        "message": {
            "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_err",
                "content": "Permission denied",
                "is_error": True,
            }],
        },
        "uuid": "test", "timestamp": "",
    })
    events = parse_jsonl_line(line)
    fmt = format_event_for_telegram(events[0])
    assert fmt is not None
    assert "Permission denied" in fmt
