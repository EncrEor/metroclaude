"""Simple rate limiting — per-user message rate + per-window tmux flood guard.

P1-SEC5: Max messages per minute per user (sliding window).
P1-SEC6: Min interval between sends to the same tmux window.

Lightweight alternative to RichardAtCT's token bucket — just what we need
for a single-user bot with potential for misuse protection.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_MAX_PER_MINUTE = 20
DEFAULT_TMUX_MIN_INTERVAL = 1.0  # seconds


class RateLimiter:
    """Simple rate limiter with per-user message count and per-window flood guard."""

    def __init__(
        self,
        max_per_minute: int = DEFAULT_MAX_PER_MINUTE,
        tmux_min_interval: float = DEFAULT_TMUX_MIN_INTERVAL,
    ) -> None:
        self._max_per_minute = max_per_minute
        self._tmux_min_interval = tmux_min_interval
        self._user_timestamps: dict[int, list[float]] = {}
        self._tmux_last_send: dict[str, float] = {}

    def check_user_rate(self, user_id: int) -> bool:
        """Check if user can send a message.

        Returns True if allowed, False if rate limited.
        Automatically cleans up timestamps older than 60s.
        """
        now = time.monotonic()
        ts = self._user_timestamps.setdefault(user_id, [])
        # Remove old timestamps (older than 60s)
        ts[:] = [t for t in ts if now - t < 60]
        if len(ts) >= self._max_per_minute:
            logger.warning(
                "Rate limited user %d: %d messages in last 60s",
                user_id, len(ts),
            )
            return False
        ts.append(now)
        return True

    def check_tmux_flood(self, window_name: str) -> bool:
        """Check if we can send to a tmux window.

        Returns True if allowed, False if too soon (flood protection).
        """
        now = time.monotonic()
        last = self._tmux_last_send.get(window_name, 0.0)
        if now - last < self._tmux_min_interval:
            return False
        self._tmux_last_send[window_name] = now
        return True

    def remaining_cooldown(self, window_name: str) -> float:
        """Get remaining cooldown time for a tmux window (seconds)."""
        now = time.monotonic()
        last = self._tmux_last_send.get(window_name, 0.0)
        remaining = self._tmux_min_interval - (now - last)
        return max(0.0, remaining)
