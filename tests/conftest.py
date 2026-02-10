"""Shared test configuration — sets required env vars before any import."""

from __future__ import annotations

import os

# Settings requires TELEGRAM_BOT_TOKEN at import time (Pydantic validation).
# Provide test defaults so tests run without a .env file.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "unused-in-tests")
os.environ.setdefault("ALLOWED_USERS", "0")
