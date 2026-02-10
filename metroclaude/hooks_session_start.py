#!/usr/bin/env python3
"""Hook script executed by Claude Code on SessionStart.

This runs INSIDE a tmux pane — no access to MetroClaude's .env or config.
It writes the session mapping to ~/.metroclaude/session_map.json.

Claude Code passes hook payload via STDIN as JSON:
  {"session_id": "UUID", "cwd": "/path/to/project", ...}

Pattern from ccbot (src/ccbot/hook.py).

Locking: uses fcntl.flock() on a separate .lock file to prevent
race conditions when multiple sessions start simultaneously.
"""

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SESSION_MAP_FILE = Path.home() / ".metroclaude" / "session_map.json"
SESSION_MAP_LOCK = SESSION_MAP_FILE.with_suffix(".lock")

# UUID v4 pattern (lowercase hex, as produced by Claude Code)
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _is_valid_session_id(value: str) -> bool:
    """Validate that session_id looks like a UUID."""
    return bool(_UUID_RE.match(value))


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON to path atomically via temp file + os.replace().

    Writes to a temp file in the same directory, then atomically
    replaces the target. This prevents partial reads if the process
    is killed mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, prefix=".session_map_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # Clean up temp file on any failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def main() -> None:
    # Read payload from stdin (Claude Code hook protocol)
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    session_id = payload.get("session_id", "")
    cwd = payload.get("cwd", os.environ.get("PWD", ""))

    if not session_id:
        return

    # Validate session_id is a proper UUID (P1-H2)
    if not _is_valid_session_id(session_id):
        return

    # Find tmux window name via $TMUX_PANE (pattern from ccbot)
    pane_id = os.environ.get("TMUX_PANE", "")
    window_name = ""
    try:
        cmd = ["tmux", "display-message", "-p", "#{session_name}:#{window_name}"]
        if pane_id:
            cmd = ["tmux", "display-message", "-t", pane_id, "-p", "#{session_name}:#{window_name}"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        window_name = result.stdout.strip()
    except Exception:
        pass

    # Ensure directory exists before acquiring lock
    SESSION_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Acquire exclusive lock, read-modify-write, release (pattern from ccbot)
    try:
        with open(SESSION_MAP_LOCK, "w") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            try:
                # Read existing map
                data: dict = {}
                if SESSION_MAP_FILE.exists():
                    try:
                        data = json.loads(SESSION_MAP_FILE.read_text())
                    except (json.JSONDecodeError, OSError):
                        data = {}

                # Store mapping: key = "session:window" (e.g. "metroclaude:general")
                # P1-H3: Always use session_name:window_name format
                # window_name from tmux display-message is already "session_name:window_name"
                if ":" in window_name:
                    key = window_name
                elif window_name:
                    # Fallback: prefix with default session name
                    key = f"metroclaude:{window_name}"
                elif pane_id:
                    key = f"metroclaude:pane-{pane_id.lstrip('%')}"
                else:
                    key = f"metroclaude:session-{session_id[:8]}"
                data[key] = {
                    "session_id": session_id,
                    "cwd": cwd,
                    "window_name": window_name,
                }

                # Atomic write (P1-H1)
                _atomic_write_json(SESSION_MAP_FILE, data)
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)
    except OSError:
        # If locking fails entirely (e.g. read-only fs), skip silently
        pass


if __name__ == "__main__":
    main()
