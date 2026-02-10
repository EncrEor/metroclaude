"""CLI entry point — python -m metroclaude."""

from __future__ import annotations

import asyncio
import logging
import sys


def main() -> None:
    from .config import get_settings

    try:
        settings = get_settings()
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        print("Copy .env.example to .env and fill in your values.", file=sys.stderr)
        sys.exit(1)

    # Setup logging — both stderr and file
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    logging.basicConfig(format=fmt, level=log_level)

    # Also log to file for persistent debugging
    file_handler = logging.FileHandler("/tmp/metroclaude.log")
    file_handler.setFormatter(logging.Formatter(fmt))
    file_handler.setLevel(log_level)
    logging.getLogger().addHandler(file_handler)

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("libtmux").setLevel(logging.WARNING)

    logger = logging.getLogger("metroclaude")
    logger.info("MetroClaude v0.1.0 starting...")
    logger.info("Working dir: %s", settings.working_dir)
    logger.info("Allowed users: %s", settings.allowed_users)

    # Check prerequisites
    import shutil

    if not shutil.which("tmux"):
        print("Error: tmux is not installed.", file=sys.stderr)
        print("  macOS: brew install tmux", file=sys.stderr)
        print("  Linux: sudo apt install tmux", file=sys.stderr)
        sys.exit(1)

    if not shutil.which(settings.claude_command):
        print(f"Error: '{settings.claude_command}' not found in PATH.", file=sys.stderr)
        print("Install Claude Code: https://docs.anthropic.com/en/docs/claude-code", file=sys.stderr)
        sys.exit(1)

    from .bot import MetroClaudeBot

    bot = MetroClaudeBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
