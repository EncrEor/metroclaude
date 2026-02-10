"""Claude Code hooks — SessionStart integration.

When Claude Code starts a new session, a hook writes the session ID
to a mapping file. The monitor can then pick it up and start polling.

IMPORTANT: The hook script (hooks_session_start.py) runs inside a tmux
pane where MetroClaude's .env is NOT loaded. It must be self-contained.
This module (hooks.py) runs inside the bot process and CAN import from
the metroclaude package.

File locking: Both the hook script and this module use fcntl.flock() on
a shared .lock file to prevent race conditions on session_map.json.

Hook format (Claude Code docs — https://code.claude.com/docs/en/hooks):
  "SessionStart": [
    {
      "matcher": "",          # regex; "" matches all session types
      "hooks": [
        {"type": "command", "command": "python3 /path/to/script.py"}
      ]
    }
  ]

Matcher values for SessionStart: "startup", "resume", "clear", "compact".
Empty string or "*" or omitting matcher entirely → match all.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Where the session map is stored
SESSION_MAP_FILE = Path.home() / ".metroclaude" / "session_map.json"
SESSION_MAP_LOCK = SESSION_MAP_FILE.with_suffix(".lock")

# Canonical name of the hook script (co-located with this module)
_HOOK_SCRIPT_NAME = "hooks_session_start.py"


def _resolve_hook_script() -> Path:
    """Resolve the absolute path to the hook script.

    Uses __file__ to locate hooks_session_start.py next to this module.
    Verifies the file actually exists to catch packaging/install issues.
    """
    script = Path(__file__).resolve().parent / _HOOK_SCRIPT_NAME
    if not script.is_file():
        raise FileNotFoundError(
            f"Hook script not found at {script}. "
            f"Ensure {_HOOK_SCRIPT_NAME} is co-located with hooks.py."
        )
    return script


def _resolve_python() -> str:
    """Resolve the python interpreter to use in the hook command.

    The hook script is self-contained (stdlib only), so it does not need
    the bot's venv. We prefer "python3" (resolved at runtime in the tmux
    shell) for portability. Falls back to sys.executable if python3 is
    not found on PATH.
    """
    # Prefer bare "python3" — it resolves at hook runtime in the tmux shell,
    # which is more robust than baking in a venv path from the bot process.
    python3 = shutil.which("python3")
    if python3:
        return "python3"
    # Fallback: full path from the current interpreter
    if sys.executable and Path(sys.executable).is_file():
        return sys.executable
    return "python3"


def _is_our_hook(command: str) -> bool:
    """Check if a hook command belongs to MetroClaude.

    Matches on the hook script filename rather than the directory name,
    which is more robust against path changes (install location, venv, etc.).
    """
    return _HOOK_SCRIPT_NAME in command


def register_hook() -> None:
    """Register MetroClaude's SessionStart hook in Claude's settings.json.

    Hook structure follows the official Claude Code format:
    https://code.claude.com/docs/en/hooks

    The matcher "" matches all SessionStart types (startup, resume, clear, compact).
    """
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

    # Resolve paths
    try:
        hook_script = _resolve_hook_script()
    except FileNotFoundError as e:
        logger.error("Cannot register hook: %s", e)
        return
    python_bin = _resolve_python()
    new_command = f"{python_bin} {hook_script}"

    # Check if already registered — update command if path changed
    for hook_group in session_start_hooks:
        for h in hook_group.get("hooks", []):
            if _is_our_hook(h.get("command", "")):
                if h["command"] != new_command:
                    logger.info("Updating hook command: %s -> %s", h["command"], new_command)
                    h["command"] = new_command
                    settings_path.write_text(json.dumps(data, indent=2))
                else:
                    logger.info("Hook already registered with correct command")
                return

    # Add our hook — matcher "" matches all SessionStart types
    session_start_hooks.append(
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": new_command,
                }
            ],
        }
    )

    settings_path.write_text(json.dumps(data, indent=2))
    logger.info("Registered SessionStart hook: %s", new_command)


def read_session_map() -> dict:
    """Read the session map file with shared lock.

    Uses fcntl.LOCK_SH (shared/read lock) so multiple readers can
    proceed concurrently, but writers (hook script) hold LOCK_EX
    which blocks readers until the write is complete.
    """
    if not SESSION_MAP_FILE.exists():
        return {}
    try:
        with open(SESSION_MAP_LOCK, "w") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_SH)
            try:
                return json.loads(SESSION_MAP_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)
    except OSError as e:
        logger.warning("Failed to acquire read lock on session_map: %s", e)
        # Fallback: read without lock (better than no data)
        try:
            return json.loads(SESSION_MAP_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}


def cleanup_stale_map_entries(live_windows: set[str]) -> int:
    """Remove session_map entries for windows that no longer exist (P1-S5).

    Keys in session_map are "session:window". We extract the window part
    and check against live_windows (which are bare window names).

    Returns the number of removed entries.
    """
    data = read_session_map()
    if not data:
        return 0
    stale_keys = []
    for k in data:
        # Extract window name from "session:window" key
        parts = k.split(":", 1)
        window_name = parts[1] if len(parts) > 1 else k
        if window_name not in live_windows:
            stale_keys.append(k)
    if not stale_keys:
        return 0
    for k in stale_keys:
        del data[k]
        logger.info("Removed stale session_map entry: %s", k)
    write_session_map(data)
    return len(stale_keys)


def write_session_map(data: dict) -> None:
    """Write to the session map file with exclusive lock + atomic write.

    Uses fcntl.LOCK_EX (exclusive lock) to prevent concurrent writes.
    Writes to a temp file first, then atomically replaces via os.replace().
    """
    SESSION_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(SESSION_MAP_LOCK, "w") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            try:
                fd, tmp_path = tempfile.mkstemp(
                    dir=SESSION_MAP_FILE.parent,
                    prefix=".session_map_",
                    suffix=".tmp",
                )
                try:
                    with os.fdopen(fd, "w") as f:
                        json.dump(data, f, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp_path, SESSION_MAP_FILE)
                except BaseException:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)
    except OSError as e:
        logger.error("Failed to write session_map: %s", e)
