"""Markdown → Telegram MarkdownV2 conversion with fallback chain.

Uses telegramify-markdown when available, falls back to plain text.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Try to import optional markdown libraries
_HAS_TELEGRAMIFY = False
try:
    import telegramify_markdown
    _HAS_TELEGRAMIFY = True
except ImportError:
    pass


def to_telegram(text: str) -> tuple[str, str]:
    """Convert markdown text to Telegram format.

    Returns:
        (formatted_text, parse_mode) where parse_mode is "MarkdownV2" or "".
    """
    if not text:
        return "", ""

    if _HAS_TELEGRAMIFY:
        try:
            converted = telegramify_markdown.markdownify(text)
            return converted, "MarkdownV2"
        except Exception as e:
            logger.debug("telegramify failed, using plain: %s", e)

    # Fallback: strip markdown for plain text
    return _strip_markdown(text), ""


def _strip_markdown(text: str) -> str:
    """Remove common markdown formatting for plain text display."""
    # Code blocks → keep content
    text = re.sub(r"```[\w]*\n(.*?)```", r"\1", text, flags=re.DOTALL)
    # Inline code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    # Headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Links [text](url) → text (url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    return text
