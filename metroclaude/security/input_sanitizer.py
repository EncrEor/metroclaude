"""Input sanitization — strip dangerous characters before tmux send-keys.

Defense-in-depth against control character injection, ESC sequences,
and command injection via backticks or $() subshells.
Inspired by RichardAtCT/claude-code-telegram SecurityValidator.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Control chars 0x00-0x1F except allowed ones (\t=0x09, \n=0x0A)
_CONTROL_CHAR_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f]"
)

# ESC sequences: ESC (0x1B) followed by anything up to a letter or end
_ESC_SEQ_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
)

# DEL character
_DEL_RE = re.compile(r"\x7f")

# Command injection patterns: backticks and $(...) subshells
_CMD_INJECTION_RE = re.compile(
    r"`[^`]*`"        # backtick command substitution
    r"|\$\([^)]*\)"   # $() command substitution
)

DEFAULT_MAX_LENGTH = 4000


def sanitize_tmux_input(text: str, *, max_length: int = DEFAULT_MAX_LENGTH) -> str:
    """Sanitize user input before sending to tmux.

    Strips control characters, ESC sequences, DEL, and command injection
    patterns. Truncates to *max_length* characters.

    Returns the cleaned string (may be shorter or identical to input).
    Logs a warning whenever content is modified.
    """
    original = text

    # 1. Strip ESC sequences first (multi-char, must go before single-char strip)
    text = _ESC_SEQ_RE.sub("", text)

    # 2. Strip control characters (except \t and \n)
    text = _CONTROL_CHAR_RE.sub("", text)

    # 3. Strip DEL
    text = _DEL_RE.sub("", text)

    # 4. Strip command injection patterns (P1-SEC2: log detection)
    if _CMD_INJECTION_RE.search(text):
        logger.warning(
            "Command injection detected and stripped: %s",
            _CMD_INJECTION_RE.findall(text)[:3],  # Log up to 3 matches
        )
    text = _CMD_INJECTION_RE.sub("", text)

    # 5. Truncate
    if len(text) > max_length:
        text = text[:max_length]

    # Log if anything changed
    if text != original:
        removed = len(original) - len(text)
        logger.warning(
            "Sanitized tmux input: removed %d chars (original %d -> %d)",
            removed,
            len(original),
            len(text),
        )

    return text


def sanitize_path_argument(path: str) -> str:
    """Sanitize a path argument (e.g. from /new command).

    Lighter than full tmux sanitization: strips control chars and
    null bytes but keeps the path structure intact.
    """
    original = path

    # Strip control characters and ESC sequences
    path = _ESC_SEQ_RE.sub("", path)
    path = _CONTROL_CHAR_RE.sub("", path)
    path = _DEL_RE.sub("", path)

    # Strip null bytes explicitly
    path = path.replace("\x00", "")

    if path != original:
        logger.warning(
            "Sanitized path argument: '%s' -> '%s'",
            original[:80],
            path[:80],
        )

    return path
