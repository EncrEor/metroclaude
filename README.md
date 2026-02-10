# MetroClaude

> Pilot Claude Code from your phone via Telegram.

MetroClaude bridges Telegram and Claude Code through tmux. Send prompts, approve permissions, resume sessions — all from your phone. Your Mac does the work.

**1 Telegram topic = 1 tmux window = 1 Claude Code session**

```
Phone (Telegram)         Mac (MetroClaude)           tmux
 +-----------+     long    +-------------+   send-keys   +--------+
 |  Topic A  | ---------> | bot.py      | ------------> | claude |
 |           | <--------- | monitor.py  | <------------ | (JSONL)|
 +-----------+    reply    +-------------+   byte-offset +--------+
```

You can switch between `tmux attach` and Telegram without losing context.

## Features

- **Interactive UI** — permission prompts, AskUserQuestion, plan mode as inline keyboards
- **Tool pairing** — tool_use messages update with checkmark/cross when result arrives
- **Smart queue** — auto-merge consecutive messages, split at 4096 chars, rate-limit retries
- **Markdown** — `telegramify-markdown` with expandable blockquotes and plain text fallback
- **Security** — user whitelist, input sanitization, tmux injection prevention, rate limiting
- **Session management** — persist across restarts, resume recent sessions, stale cleanup
- **Typing indicator** — shows "typing..." while Claude works, stops on response
- **Crash detection** — detects Claude exit, offers restart button with `--resume`
- **Forward commands** — `/clear`, `/compact`, `/cost` forwarded to Claude automatically

## Quick start

### Prerequisites

- macOS or Linux
- Python 3.11+
- tmux (`brew install tmux`)
- Claude Code CLI (`claude` in your PATH)
- A Telegram bot token ([@BotFather](https://t.me/BotFather))
- A Telegram group with Topics enabled

### Install

```bash
git clone https://github.com/EncrEor/metroclaude.git
cd metroclaude
pip install -e ".[markdown,dev]"
```

### Configure

```bash
cp .env.example .env
```

Edit `.env`:

```bash
TELEGRAM_BOT_TOKEN=your-token-from-botfather
ALLOWED_USERS=your-telegram-user-id
```

> Get your user ID from [@userinfobot](https://t.me/userinfobot) on Telegram.

### Run

```bash
python -m metroclaude
```

Or if installed:

```bash
metroclaude
```

## Telegram commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/new [path]` | Start new Claude session in this topic |
| `/stop` | Stop session and kill tmux window |
| `/status` | List all active sessions |
| `/resume` | Resume a recent session (inline keyboard) |
| `/screenshot` | Capture current terminal content |

Any unrecognized `/command` (like `/clear`, `/compact`, `/cost`) is forwarded directly to Claude.

## How it works

### Message flow

1. You type in a Telegram topic
2. MetroClaude sanitizes the input and sends it to the matching tmux window via `send-keys`
3. Claude Code processes the prompt and writes to `~/.claude/projects/.../session.jsonl`
4. The JSONL monitor detects new bytes (byte-offset tracking, 2s polling)
5. The parser extracts text, tool_use, and tool_result events
6. The message queue formats, merges, and sends to Telegram with markdown

### Interactive prompts

When Claude asks for permission or a question:
- The status poller captures the terminal every 2s
- Regex detects permission prompts, AskUserQuestion, plan mode
- An inline keyboard is sent to Telegram
- Your button press sends the keystrokes back to tmux

### Session lifecycle

- `/new` creates a tmux window, starts `claude`, waits for the SessionStart hook to register the session ID
- The hook script writes to `~/.metroclaude/session_map.json` (with file locking)
- The monitor starts polling the JSONL file
- `/stop` kills the window and cleans up
- Stale sessions (dead tmux windows) are automatically cleaned every 30s

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | -- | Bot token from @BotFather |
| `ALLOWED_USERS` | Yes | -- | Comma-separated Telegram user IDs |
| `TMUX_SESSION_NAME` | No | `metroclaude` | tmux session name |
| `CLAUDE_COMMAND` | No | `claude` | Claude CLI command |
| `MONITOR_POLL_INTERVAL` | No | `2.0` | JSONL polling interval (seconds) |
| `LOG_LEVEL` | No | `INFO` | DEBUG, INFO, WARNING, ERROR |
| `WORKING_DIR` | No | `~/Documents/Joy_Claude` | Default project directory |

## Project structure

```
metroclaude/
  __main__.py          # CLI entry point
  config.py            # Pydantic Settings (.env)
  bot.py               # Telegram polling, routing, event dispatch
  tmux.py              # Async libtmux wrapper
  monitor.py           # JSONL byte-offset polling
  parser.py            # JSONL event parser
  session.py           # Session state + JSON persistence
  hooks.py             # Claude Code SessionStart hook registration
  hooks_session_start.py  # Hook script (runs in tmux pane)
  exceptions.py        # Typed exception hierarchy
  handlers/
    commands.py        # /new, /stop, /status, /resume, /screenshot
    message.py         # Text messages + forward commands
    interactive.py     # Inline keyboards for permissions/questions
    callback_data.py   # Callback encoding/decoding
    status.py          # Typing manager + terminal detection
  security/
    auth.py            # User whitelist
    input_sanitizer.py # Tmux injection prevention
    rate_limiter.py    # Per-user + per-window rate limiting
  utils/
    queue.py           # Message queue with tool pairing
    markdown.py        # Markdown -> Telegram formatting
tests/
  test_parser.py       # JSONL parser (10 tests)
  test_queue.py        # Message queue (11 tests)
  test_interactive.py  # Interactive UI (16 tests)
  test_security.py     # Auth + sanitization (19 tests)
  test_tmux.py         # Tmux manager (18 tests)
  test_rate_limiter.py # Rate limiting (11 tests)
  test_session.py      # Session manager (8 tests)
```

## Security

MetroClaude runs locally on your Mac. No cloud servers, no API proxying.

- **User whitelist** — only `ALLOWED_USERS` can interact with the bot
- **Input sanitization** — control characters, backtick injection, `$(...)` patterns stripped before tmux
- **Path validation** — `/new` only accepts directories under `$HOME`
- **Rate limiting** — 20 messages/minute per user, 1s minimum between tmux sends
- **Generic errors** — tracebacks logged, not exposed to Telegram
- **File locking** — `fcntl.flock()` on session_map.json for concurrent access

## Inspired by

MetroClaude is a "best of" from 7 open-source projects:

| Project | What we took |
|---------|-------------|
| [ccbot](https://github.com/six-ddc/ccbot) | tmux bridge, JSONL monitor, message queue, interactive UI |
| [claude-code-telegram](https://github.com/RichardAtCT/claude-code-telegram) | 5-layer security, session management |
| [claudecode-telegram](https://github.com/hanxiao/claudecode-telegram) | Typing indicator, blocked commands, markdown |
| [claude-telegram-bot](https://github.com/linuz90/claude-telegram-bot) | Session resume, crash recovery |
| [bot-on-anything](https://github.com/zhayujie/bot-on-anything) | Channel abstraction concept |
| [vibeIDE](https://github.com/junecv/vibeIDE) | Agent SDK patterns |
| [Claude-Code-Remote](https://github.com/JessyTsui/Claude-Code-Remote) | Hook-based architecture |

## Development

```bash
# Install with dev dependencies
pip install -e ".[markdown,dev]"

# Run tests (93 tests)
pytest tests/ -v

# Run with debug logging
LOG_LEVEL=DEBUG python -m metroclaude
```

## License

MIT
