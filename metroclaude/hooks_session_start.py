#!/usr/bin/env python3
"""Hook script executed by Claude Code on SessionStart.

This runs INSIDE a tmux pane — no access to MetroClaude's .env or config.
It writes the session mapping to ~/.metroclaude/session_map.json.

Environment variables provided by Claude Code hooks:
- CLAUDE_SESSION_ID: The session UUID
- CLAUDE_CWD or PWD: Working directory
"""

import json
import os
import sys
from pathlib import Path

SESSION_MAP_FILE = Path.home() / ".metroclaude" / "session_map.json"


def main() -> None:
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    cwd = os.environ.get("CLAUDE_CWD", os.environ.get("PWD", ""))
    tmux_window = os.environ.get("TMUX_PANE", "")  # tmux pane ID

    if not session_id:
        return

    # Read existing map
    data: dict = {}
    if SESSION_MAP_FILE.exists():
        try:
            data = json.loads(SESSION_MAP_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}

    # Find tmux window name from tmux environment
    window_name = ""
    try:
        import subprocess
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#{window_name}"],
            capture_output=True, text=True, timeout=5,
        )
        window_name = result.stdout.strip()
    except Exception:
        pass

    # Store mapping
    data[window_name or tmux_window or session_id] = {
        "session_id": session_id,
        "cwd": cwd,
        "window_name": window_name,
    }

    SESSION_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_MAP_FILE.write_text(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
