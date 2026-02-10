"""Claude Code hooks — SessionStart integration.

When Claude Code starts a new session, a hook writes the session ID
to a mapping file. The monitor can then pick it up and start polling.

IMPORTANT: This script runs inside a tmux pane where MetroClaude's .env
is NOT loaded. It must be self-contained (no imports from config.py).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Where the session map is stored
SESSION_MAP_FILE = Path.home() / ".metroclaude" / "session_map.json"


def register_hook() -> None:
    """Register MetroClaude's SessionStart hook in Claude's settings.json."""
    settings_path = Path.home() / ".claude" / "settings.json"

    if not settings_path.exists():
        logger.warning("Claude settings.json not found at %s", settings_path)
        return

    try:
        data = json.loads(settings_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read Claude settings: %s", e)
        return

    hooks = data.setdefault("hooks", {})
    session_start_hooks = hooks.setdefault("SessionStart", [])

    # Our hook script path
    hook_script = Path(__file__).parent / "hooks_session_start.py"

    # Check if already registered
    for hook_group in session_start_hooks:
        for h in hook_group.get("hooks", []):
            if "metroclaude" in h.get("command", ""):
                logger.info("Hook already registered")
                return

    # Add our hook
    session_start_hooks.append({
        "matcher": "",  # Match all sessions
        "hooks": [{
            "type": "command",
            "command": f"python3 {hook_script}",
        }],
    })

    settings_path.write_text(json.dumps(data, indent=2))
    logger.info("Registered SessionStart hook in %s", settings_path)


def read_session_map() -> dict:
    """Read the session map file."""
    if not SESSION_MAP_FILE.exists():
        return {}
    try:
        return json.loads(SESSION_MAP_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def write_session_map(data: dict) -> None:
    """Write to the session map file."""
    SESSION_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_MAP_FILE.write_text(json.dumps(data, indent=2))
