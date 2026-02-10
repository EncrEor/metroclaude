"""Authentication — Telegram user ID whitelist.

P1-SEC1: Unauthorized users get a feedback message (not silent ignore).
P1-SEC7: All auth failures are logged with user details.
P2-SEC6: Structured audit logging for auth success/failure.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update

from ..config import get_settings

if TYPE_CHECKING:
    from .audit import AuditLogger

logger = logging.getLogger(__name__)


def is_authorized(user_id: int) -> bool:
    """Check if a Telegram user ID is in the whitelist."""
    allowed = get_settings().get_allowed_user_ids()
    if not allowed:
        logger.warning("ALLOWED_USERS is empty — all users blocked")
        return False
    return user_id in allowed


async def check_auth(
    update: Update,
    audit: AuditLogger | None = None,
) -> bool:
    """Check authorization and send feedback if denied (P1-SEC1 + P1-SEC7).

    Returns True if authorized, False otherwise.
    Use this in handlers instead of bare is_authorized() to get
    proper feedback and logging.

    Pass ``audit`` for structured audit logging (P2-SEC6).
    """
    user = update.effective_user
    if not user:
        return False

    if is_authorized(user.id):
        # P2-SEC6: Audit success
        if audit:
            await audit.log_auth_success(
                user_id=user.id,
                username=user.username or "",
            )
        return True

    # P1-SEC7: Log auth failure with details
    logger.warning(
        "Unauthorized access attempt: user_id=%d username=%s name='%s'",
        user.id,
        user.username or "N/A",
        user.full_name or "N/A",
    )

    # P2-SEC6: Audit failure
    if audit:
        await audit.log_auth_failure(
            user_id=user.id,
            username=user.username or "",
            name=user.full_name or "",
        )

    # P1-SEC1: Send feedback (once, don't spam)
    if update.message:
        try:
            await update.message.reply_text("Non autorise.")
        except Exception:
            pass  # Don't fail on feedback errors

    return False
