"""Markdown -> Telegram MarkdownV2 conversion with fallback chain.

Uses telegramify-markdown when available, falls back to plain text.

Key improvements over defaults:
- Disables indented code blocks (4-space indent != code in Claude output)
- Enables expandable blockquotes for long quoted sections
- Truncates overly long code blocks to stay within Telegram limits
- Cleaner heading symbols (no emoji prefixes)
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency: telegramify-markdown + mistletoe
# Everything below the guard works without these packages installed.
# ---------------------------------------------------------------------------
_HAS_TELEGRAMIFY = False
_telegramify_markdown = None
_TelegramMarkdownRenderer = None
_escape_latex = None
_update_block = None
_remove_token = None
_BlockCode = None
_mistletoe = None

try:
    import mistletoe as _mistletoe
    import telegramify_markdown as _telegramify_markdown  # noqa: F401 (side-effect import)
    from mistletoe.block_token import BlockCode as _BlockCode
    from mistletoe.block_token import remove_token as _remove_token
    from telegramify_markdown import _update_block
    from telegramify_markdown import escape_latex as _escape_latex

    # ---- Module-level configuration (runs once at import) ----
    from telegramify_markdown.customize import get_runtime_config
    from telegramify_markdown.render import (
        TelegramMarkdownRenderer as _TelegramMarkdownRenderer,
    )

    _cfg = get_runtime_config()

    # Enable expandable blockquotes for long quoted sections (>200 chars).
    # Telegram renders these with a "show more" toggle.
    _cfg.cite_expandable = True

    # Use clean heading symbols instead of emoji (cleaner for CLI output).
    _cfg.markdown_symbol.head_level_1 = "\u25b6"  # Black right-pointing triangle
    _cfg.markdown_symbol.head_level_2 = "\u25b7"  # White right-pointing triangle
    _cfg.markdown_symbol.head_level_3 = "\u2022"  # Bullet
    _cfg.markdown_symbol.head_level_4 = "\u2022"  # Bullet

    _HAS_TELEGRAMIFY = True
    logger.debug("telegramify-markdown loaded, configuration applied")

except ImportError:
    logger.debug("telegramify-markdown not installed, using plain text fallback")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum length (in characters) for a single fenced code block before
# truncation.  Keeps messages within Telegram's 4096-char limit when code
# blocks dominate the output.
_MAX_CODE_BLOCK_LENGTH = 2000

# Truncation suffix appended when a code block is cut.
_TRUNCATION_SUFFIX = "\n... (truncated)"


# ---------------------------------------------------------------------------
# Pre-processing: truncate long code blocks BEFORE markdown conversion
# ---------------------------------------------------------------------------


def _truncate_code_blocks(text: str, max_length: int = _MAX_CODE_BLOCK_LENGTH) -> str:
    """Truncate fenced code blocks that exceed *max_length* characters.

    Operates on the raw markdown text before any conversion. Only fenced
    blocks (``` delimited) are affected -- indented blocks are ignored
    (and disabled in the renderer anyway).
    """
    if max_length <= 0:
        return text

    def _truncate_match(m: re.Match[str]) -> str:
        opening = m.group(1)  # ```lang\n
        body = m.group(2)  # code content
        closing = m.group(3)  # ```
        if len(body) <= max_length:
            return m.group(0)
        truncated = body[:max_length].rstrip()
        return f"{opening}{truncated}{_TRUNCATION_SUFFIX}\n{closing}"

    return re.sub(
        r"(```\w*\n)(.*?)(```)",
        _truncate_match,
        text,
        flags=re.DOTALL,
    )


# ---------------------------------------------------------------------------
# Core conversion: custom markdownify that disables indented code blocks
# ---------------------------------------------------------------------------


def _markdownify(text: str) -> str:
    """Convert standard Markdown to Telegram MarkdownV2.

    Uses TelegramMarkdownRenderer directly (instead of the top-level
    ``telegramify_markdown.markdownify``) so we can call
    ``remove_token(BlockCode)`` inside the context manager -- this
    disables the indented-code-block rule that causes false positives
    with Claude's output (4 spaces of indentation != code).

    Fenced code blocks (``` delimited) remain fully supported.

    Inspired by ccbot's approach:
    https://github.com/six-ddc/ccbot/blob/main/src/ccbot/markdown_v2.py
    """
    with _TelegramMarkdownRenderer(normalize_whitespace=False) as renderer:
        # Disable indented code blocks inside the context manager.
        # The renderer's __exit__ resets tokens, so this is safe.
        _remove_token(_BlockCode)

        content = _escape_latex(text)
        document = _mistletoe.Document(content)
        _update_block(document)
        return renderer.render(document)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def to_telegram(
    text: str,
    *,
    max_block_length: int = _MAX_CODE_BLOCK_LENGTH,
) -> tuple[str, str]:
    """Convert markdown text to Telegram MarkdownV2 format.

    Args:
        text: Raw markdown text (e.g. Claude's response).
        max_block_length: Maximum character length for a single fenced code
            block.  Blocks exceeding this are truncated with an ellipsis.
            Set to 0 to disable truncation.

    Returns:
        ``(formatted_text, parse_mode)`` where *parse_mode* is
        ``"MarkdownV2"`` when conversion succeeds, or ``""`` for plain text
        fallback.
    """
    if not text:
        return "", ""

    if _HAS_TELEGRAMIFY:
        try:
            processed = _truncate_code_blocks(text, max_length=max_block_length)
            converted = _markdownify(processed)
            return converted, "MarkdownV2"
        except (ValueError, TypeError) as exc:
            # Structural issues in the markdown AST (e.g. token mismatch).
            logger.warning("telegramify structural error, using plain text: %s", exc)
        except Exception as exc:
            # Catch-all for unexpected rendering failures.
            logger.debug("telegramify failed, using plain text: %s", exc)

    # Fallback: strip markdown for plain text display
    return _strip_markdown(text), ""


# ---------------------------------------------------------------------------
# Fallback: plain-text stripping
# ---------------------------------------------------------------------------


def _strip_markdown(text: str) -> str:
    """Remove common markdown formatting for plain text display.

    Handles: fenced code blocks, inline code, bold, italic, strikethrough,
    headings, links, images, blockquotes, horizontal rules.
    """
    # Fenced code blocks -> keep content only
    text = re.sub(r"```[\w]*\n(.*?)```", r"\1", text, flags=re.DOTALL)
    # Inline code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Bold + italic combined (***text***)
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", text)
    # Bold (**text**)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    # Italic (*text*)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    # Strikethrough (~~text~~)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    # Headers (# ... ######)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Images ![alt](url) -> alt
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    # Links [text](url) -> text (url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    # Blockquotes (> prefix) -> remove prefix
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    # Horizontal rules (---, ***, ___) -> empty line
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    return text
