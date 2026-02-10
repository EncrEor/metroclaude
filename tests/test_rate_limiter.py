"""Tests for Batch 6 — rate limiting and forward commands."""

import time
from unittest.mock import patch

from metroclaude.security.rate_limiter import RateLimiter


# ------------------------------------------------------------------
# P1-SEC5: Per-user rate limiting
# ------------------------------------------------------------------

def test_rate_limiter_allows_normal_traffic():
    """Normal traffic should pass through."""
    rl = RateLimiter(max_per_minute=5)
    for _ in range(5):
        assert rl.check_user_rate(123) is True


def test_rate_limiter_blocks_excess():
    """Excess messages should be blocked."""
    rl = RateLimiter(max_per_minute=3)
    assert rl.check_user_rate(123) is True
    assert rl.check_user_rate(123) is True
    assert rl.check_user_rate(123) is True
    assert rl.check_user_rate(123) is False  # 4th should be blocked


def test_rate_limiter_per_user_isolation():
    """Different users should have separate limits."""
    rl = RateLimiter(max_per_minute=2)
    assert rl.check_user_rate(123) is True
    assert rl.check_user_rate(123) is True
    assert rl.check_user_rate(123) is False  # User 123 blocked
    assert rl.check_user_rate(456) is True   # User 456 still OK


def test_rate_limiter_cleans_old_timestamps():
    """Old timestamps (>60s) should be cleaned up."""
    rl = RateLimiter(max_per_minute=2)
    # Manually inject old timestamps
    rl._user_timestamps[123] = [time.monotonic() - 120, time.monotonic() - 90]
    # Should be cleaned, allowing new messages
    assert rl.check_user_rate(123) is True


# ------------------------------------------------------------------
# P1-SEC6: Tmux flood protection
# ------------------------------------------------------------------

def test_tmux_flood_allows_first_send():
    """First send to a window should always be allowed."""
    rl = RateLimiter(tmux_min_interval=1.0)
    assert rl.check_tmux_flood("test-window") is True


def test_tmux_flood_blocks_rapid_fire():
    """Rapid sends to same window should be blocked."""
    rl = RateLimiter(tmux_min_interval=1.0)
    assert rl.check_tmux_flood("test-window") is True
    assert rl.check_tmux_flood("test-window") is False  # Too soon


def test_tmux_flood_per_window_isolation():
    """Different windows should have separate flood timers."""
    rl = RateLimiter(tmux_min_interval=1.0)
    assert rl.check_tmux_flood("window-1") is True
    assert rl.check_tmux_flood("window-2") is True  # Different window, OK
    assert rl.check_tmux_flood("window-1") is False  # Same window, blocked


def test_remaining_cooldown():
    """remaining_cooldown should return positive value right after send."""
    rl = RateLimiter(tmux_min_interval=5.0)
    rl.check_tmux_flood("test")
    cooldown = rl.remaining_cooldown("test")
    assert cooldown > 0
    assert cooldown <= 5.0


def test_remaining_cooldown_no_prior_send():
    """remaining_cooldown should return 0 for never-used window."""
    rl = RateLimiter(tmux_min_interval=1.0)
    assert rl.remaining_cooldown("new-window") == 0.0


# ------------------------------------------------------------------
# P1-B1: Forward command handler (structure test)
# ------------------------------------------------------------------

def test_forward_command_exists():
    """handle_forward_command should be importable."""
    from metroclaude.handlers.message import handle_forward_command
    assert callable(handle_forward_command)


# ------------------------------------------------------------------
# P1-B2: Topic closed handler (structure test)
# ------------------------------------------------------------------

def test_topic_closed_filter_exists():
    """FORUM_TOPIC_CLOSED filter should exist."""
    from telegram.ext import filters
    assert hasattr(filters.StatusUpdate, "FORUM_TOPIC_CLOSED")
