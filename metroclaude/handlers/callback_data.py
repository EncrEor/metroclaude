"""Callback data encoding/decoding for Telegram inline keyboards.

Format: "PREFIX:payload" where payload is window_name or "index:window_name".
Total length must be < 64 bytes (Telegram limit).
"""

# Callback prefixes — each type of interactive UI has its own prefix
CB_PERMIT_YES = "py"      # Permission: approve
CB_PERMIT_NO = "pn"       # Permission: deny
CB_ASKUSER = "au"          # AskUserQuestion: select option by index
CB_PLANMODE_YES = "ey"    # ExitPlanMode: proceed
CB_PLANMODE_NO = "en"     # ExitPlanMode: cancel
CB_RESTART = "rs"          # Restart Claude after exit
CB_REFRESH = "rf"          # Refresh terminal capture
CB_RESTORE_YES = "ry"     # RestoreCheckpoint: yes
CB_RESTORE_NO = "rn"      # RestoreCheckpoint: no

# Map prefixes to the tmux key to send
PREFIX_TO_TMUX_KEY = {
    CB_PERMIT_YES: "y",
    CB_PERMIT_NO: "n",
    CB_PLANMODE_YES: "y",
    CB_PLANMODE_NO: "n",
    CB_RESTORE_YES: "y",
    CB_RESTORE_NO: "n",
}


def encode_callback(prefix: str, window_name: str, index: int | None = None) -> str:
    """Encode callback data. Truncates window_name to fit 64 byte limit."""
    if index is not None:
        payload = f"{index}:{window_name}"
    else:
        payload = window_name
    data = f"{prefix}:{payload}"
    return data[:64]  # Telegram limit


def decode_callback(data: str) -> tuple[str, str, int | None]:
    """Decode callback data -> (prefix, window_name, index_or_none)."""
    parts = data.split(":", 2)
    prefix = parts[0]
    if len(parts) == 3:
        # Has index: "au:2:window-name"
        try:
            index = int(parts[1])
        except ValueError:
            index = None
        window_name = parts[2]
    elif len(parts) == 2:
        index = None
        window_name = parts[1]
    else:
        index = None
        window_name = ""
    return prefix, window_name, index
