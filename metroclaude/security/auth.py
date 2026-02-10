"""Authentication — Telegram user ID whitelist."""

from __future__ import annotations

import logging

from ..config import get_settings

logger = logging.getLogger(__name__)


def is_authorized(user_id: int) -> bool:
    """Check if a Telegram user ID is in the whitelist."""
    allowed = get_settings().allowed_users
    if not allowed:
        logger.warning("ALLOWED_USERS is empty — all users blocked")
        return False
    return user_id in allowed
