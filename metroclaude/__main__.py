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

    # Setup logging
    logging.basicConfig(
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)

    logger = logging.getLogger("metroclaude")
    logger.info("MetroClaude v0.1.0 starting...")
    logger.info("Working dir: %s", settings.working_dir)
    logger.info("Allowed users: %s", settings.allowed_users)

    from .bot import MetroClaudeBot

    bot = MetroClaudeBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
