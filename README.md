# MetroClaude

> Pilot Claude Code from your phone via Telegram.

MetroClaude bridges Telegram and Claude Code CLI through tmux, letting you send prompts and receive responses from anywhere — your phone, tablet, or another machine.

**Your Mac stays on.** The bot runs locally. Zero new infrastructure.

## How it works

```
Phone (Telegram)  →  MetroClaude bot (Mac)  →  tmux  →  Claude Code CLI
                  ←  JSONL polling          ←         ←
```

**1 Telegram topic = 1 tmux window = 1 Claude Code session**

You can switch between terminal (`tmux attach`) and Telegram without losing context.

## Quick start

### Prerequisites

- Python 3.11+
- tmux (`brew install tmux`)
- Claude Code CLI (`claude` in your PATH)
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))

### Install

```bash
git clone https://github.com/YOUR_USERNAME/metroclaude.git
cd metroclaude
pip install -e ".[markdown]"
```

### Configure

```bash
cp .env.example .env
# Edit .env — set TELEGRAM_BOT_TOKEN and ALLOWED_USERS
```

### Run

```bash
python -m metroclaude
```

## Telegram commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/new [path]` | Start new Claude session in this topic |
| `/stop` | Stop session in this topic |
| `/status` | List all active sessions |
| `/resume` | Resume a recent session (inline keyboard) |
| `/screenshot` | Capture terminal content |

## Architecture

Built as a "best of" from 7 open-source projects:

| Component | Inspired by |
|-----------|-------------|
| tmux bridge | [ccbot](https://github.com/six-ddc/ccbot) |
| JSONL monitor | [ccbot](https://github.com/six-ddc/ccbot) |
| Message queue | [ccbot](https://github.com/six-ddc/ccbot) |
| Security layers | [claude-code-telegram](https://github.com/RichardAtCT/claude-code-telegram) |
| Typing indicator | [claudecode-telegram](https://github.com/hanxiao/claudecode-telegram) |
| Session resume | [claude-telegram-bot](https://github.com/linuz90/claude-telegram-bot) |
| Channel abstraction | [bot-on-anything](https://github.com/zhayujie/bot-on-anything) |

### Key features

- **Async throughout** — `asyncio.to_thread()` wraps all blocking tmux calls
- **Byte-offset JSONL polling** — never re-reads old data, handles partial lines
- **Auto-merge messages** — reduces Telegram spam during long Claude outputs
- **Smart splitting** — respects 4096 char limit, splits at newlines
- **Markdown formatting** — `telegramify-markdown` with plain text fallback
- **User whitelist** — only authorized Telegram users can interact
- **Blocked commands** — interactive Claude commands (`/settings`, `/vim`, etc.) are filtered

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | — | From @BotFather |
| `ALLOWED_USERS` | Yes | — | Comma-separated Telegram user IDs |
| `TMUX_SESSION_NAME` | No | `metroclaude` | tmux session name |
| `CLAUDE_COMMAND` | No | `claude` | Path to Claude CLI |
| `MONITOR_POLL_INTERVAL` | No | `2.0` | JSONL polling interval (seconds) |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `WORKING_DIR` | No | `~/Documents/Joy_Claude` | Default project directory |

## License

MIT
