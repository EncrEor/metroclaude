# Changelog

All notable changes to MetroClaude will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.1.0] - 2026-02-10

### Added
- Telegram bot with long polling and topic-based routing
- tmux bridge: async libtmux wrapper with injection-safe `send-keys -l`
- JSONL monitor with byte-offset tracking (no re-reads)
- Transcript parser: text, tool_use, tool_result, thinking blocks
- Message queue with auto-merge, rate-limit retries, 4096-char splitting
- Interactive UI: inline keyboards for permissions, AskUserQuestion, plan mode
- Tool pairing: tool_use messages update with result status
- Session management with JSON persistence and stale cleanup
- Typing indicator while Claude works
- Crash detection with restart button (`--resume`)
- Forward commands (`/clear`, `/compact`, `/cost`) to Claude
- Security: user whitelist, input sanitization, tmux injection prevention
- Security: per-user and per-window rate limiting
- Security: structured audit logging (9 event types, 4 risk levels)
- Claude Code SessionStart hook with file locking
- Markdown to Telegram formatting with plain text fallback
- 104 tests across 7 test files

[0.1.0]: https://github.com/EncrEor/metroclaude/releases/tag/v0.1.0
