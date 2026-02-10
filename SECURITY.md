# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting a vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT open a public issue**
2. Use [GitHub Private Vulnerability Reporting](https://github.com/EncrEor/metroclaude/security/advisories/new)
3. Or email: ahmed@joyjuice.co

We will acknowledge receipt within 48 hours and provide an assessment within 7 days.

## Security model

MetroClaude runs **locally on your machine**. No cloud servers, no API proxying. Your Claude Code sessions never leave your computer.

- **User whitelist** — only configured Telegram user IDs can interact
- **Input sanitization** — control characters, backtick injection, `$(...)` patterns stripped before tmux
- **Rate limiting** — per-user and per-window flood protection
- **Audit logging** — structured event logging with risk levels
- **Path validation** — `/new` only accepts directories under `$HOME`
- **File locking** — `fcntl.flock()` on shared state files
