"""Configuration via Pydantic Settings — loads from .env or environment."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # Required
    telegram_bot_token: str
    allowed_users: str = ""  # Comma-separated user IDs (e.g. "123,456")

    # tmux
    tmux_session_name: str = "metroclaude"
    claude_command: str = "claude"

    # Monitor
    monitor_poll_interval: float = 2.0

    # Paths
    working_dir: Path = Path.home() / "Documents" / "Joy_Claude"
    state_dir: Path = Path.home() / ".metroclaude"
    claude_projects_dir: Path = Path.home() / ".claude" / "projects"

    # Logging
    log_level: str = "INFO"

    # Message limits
    telegram_max_message_length: int = 4096
    message_merge_delay: float = 0.5  # seconds to wait before merging
    message_merge_max_length: int = 3800  # leave room for formatting

    # Blocked Claude commands (interactive, would crash in Telegram)
    blocked_commands: list[str] = [
        "/mcp",
        "/help",
        "/settings",
        "/config",
        "/model",
        "/compact",
        "/cost",
        "/doctor",
        "/init",
        "/login",
        "/logout",
        "/memory",
        "/permissions",
        "/pr",
        "/review",
        "/terminal",
        "/vim",
        "/approved-tools",
        "/listen",
    ]

    def get_allowed_user_ids(self) -> list[int]:
        """Parse allowed_users CSV string into list of ints."""
        if not self.allowed_users or not self.allowed_users.strip():
            return []
        return [int(uid.strip()) for uid in self.allowed_users.split(",") if uid.strip()]

    @field_validator("working_dir", "state_dir", "claude_projects_dir", mode="before")
    @classmethod
    def expand_path(cls, v: str | Path) -> Path:
        return Path(os.path.expanduser(str(v)))

    def ensure_dirs(self) -> None:
        """Create required directories if they don't exist."""
        self.state_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    """Load settings (cached singleton)."""
    if not hasattr(get_settings, "_instance"):
        get_settings._instance = Settings()  # type: ignore[attr-defined]
        get_settings._instance.ensure_dirs()
    return get_settings._instance  # type: ignore[attr-defined]
