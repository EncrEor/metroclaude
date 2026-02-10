"""Transcript parser — extract meaningful events from Claude Code JSONL.

Each JSONL line is one of: user, assistant, system, file-history-snapshot, progress.
We extract assistant text and tool summaries for Telegram display.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    TEXT = "text"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    SYSTEM = "system"
    PROGRESS = "progress"


@dataclass
class ParsedEvent:
    event_type: EventType
    content: str = ""
    tool_name: str = ""
    tool_id: str = ""
    tool_input_summary: str = ""
    is_error: bool = False
    uuid: str = ""
    timestamp: str = ""
    raw: dict = field(default_factory=dict)


# Tool input summarizers — extract the most relevant field for display
_TOOL_SUMMARIES: dict[str, str] = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
    "Bash": "command",
    "WebFetch": "url",
    "WebSearch": "query",
    "Task": "description",
}


def _summarize_tool_input(name: str, inputs: dict) -> str:
    """Create a short summary of tool inputs for display."""
    key = _TOOL_SUMMARIES.get(name)
    if key and key in inputs:
        val = str(inputs[key])
        if len(val) > 80:
            val = val[:77] + "..."
        return val
    # Fallback: first string value
    for v in inputs.values():
        if isinstance(v, str) and v:
            s = v[:80]
            return s + "..." if len(v) > 80 else s
    return ""


def parse_jsonl_line(line: str) -> list[ParsedEvent]:
    """Parse a single JSONL line into zero or more events."""
    line = line.strip()
    if not line:
        return []

    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return []

    msg_type = data.get("type", "")
    uuid = data.get("uuid", "")
    timestamp = data.get("timestamp", "")
    events: list[ParsedEvent] = []

    if msg_type == "assistant":
        message = data.get("message", {})
        content_blocks = message.get("content", [])
        for block in content_blocks:
            block_type = block.get("type", "")

            if block_type == "text":
                text = block.get("text", "")
                if text.strip():
                    events.append(
                        ParsedEvent(
                            event_type=EventType.TEXT,
                            content=text,
                            uuid=uuid,
                            timestamp=timestamp,
                            raw=data,
                        )
                    )

            elif block_type == "tool_use":
                tool_name = block.get("name", "unknown")
                tool_id = block.get("id", "")
                tool_input = block.get("input", {})
                summary = _summarize_tool_input(tool_name, tool_input)
                events.append(
                    ParsedEvent(
                        event_type=EventType.TOOL_USE,
                        tool_name=tool_name,
                        tool_id=tool_id,
                        tool_input_summary=summary,
                        uuid=uuid,
                        timestamp=timestamp,
                        raw=data,
                    )
                )

            elif block_type == "thinking":
                # We don't forward thinking to Telegram by default
                pass

    elif msg_type == "user":
        message = data.get("message", {})
        content = message.get("content", "")
        if isinstance(content, list):
            for item in content:
                if item.get("type") == "tool_result":
                    events.append(
                        ParsedEvent(
                            event_type=EventType.TOOL_RESULT,
                            tool_id=item.get("tool_use_id", ""),
                            content=_truncate(str(item.get("content", "")), 200),
                            is_error=item.get("is_error", False),
                            uuid=uuid,
                            timestamp=timestamp,
                            raw=data,
                        )
                    )

    elif msg_type == "system":
        content = data.get("content", "")
        events.append(
            ParsedEvent(
                event_type=EventType.SYSTEM,
                content=content,
                uuid=uuid,
                timestamp=timestamp,
                raw=data,
            )
        )

    return events


def format_event_for_telegram(event: ParsedEvent) -> str | None:
    """Format a parsed event into a Telegram-friendly string.

    Returns None if the event should not be displayed.
    """
    if event.event_type == EventType.TEXT:
        return event.content

    if event.event_type == EventType.TOOL_USE:
        summary = f"({event.tool_input_summary})" if event.tool_input_summary else ""
        return f"🔧 **{event.tool_name}**{summary}"

    if event.event_type == EventType.TOOL_RESULT:
        if event.is_error:
            return f"❌ Erreur: {event.content}"
        return None  # Don't spam tool results

    if event.event_type == EventType.SYSTEM:
        return None  # System messages are internal

    return None


def _truncate(s: str, max_len: int) -> str:
    return s[: max_len - 3] + "..." if len(s) > max_len else s
