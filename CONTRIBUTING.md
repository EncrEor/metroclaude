# Contributing to MetroClaude

Thanks for your interest in contributing!

## Setup

```bash
git clone https://github.com/EncrEor/metroclaude.git
cd metroclaude
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[markdown,dev]"
```

## Running tests

```bash
pytest tests/ -v
```

All 104 tests must pass before submitting a PR.

## Code style

- Python 3.11+ with type hints
- `from __future__ import annotations` at the top of every module
- Async functions for anything that touches tmux (via `asyncio.to_thread()`)
- Pydantic Settings for configuration
- Typed exceptions (see `exceptions.py`)

## Architecture rules

1. **Best-of approach** — before implementing a feature, check how [ccbot](https://github.com/six-ddc/ccbot), [claude-code-telegram](https://github.com/RichardAtCT/claude-code-telegram), and other reference projects handle it
2. **Security first** — sanitize all user input before it reaches tmux. Never expose tracebacks to Telegram
3. **Dependency injection** — shared objects go in `context.bot_data`, not global singletons
4. **File locking** — any write to `session_map.json` must use `fcntl.flock(LOCK_EX)`
5. **Atomic writes** — state files use `tempfile.mkstemp()` + `os.replace()` pattern

## Adding a new handler

1. Create the handler function in `metroclaude/handlers/`
2. Register it in `MetroClaudeBot._register_handlers()` in `bot.py`
3. Add tests in `tests/`

## Adding a new command

1. Add the async handler in `handlers/commands.py`
2. Register with `CommandHandler("name", cmd_name)` in `bot.py`
3. Update the README command table

## PR checklist

- [ ] Tests pass (`pytest tests/ -v`)
- [ ] New features have tests
- [ ] No secrets or tokens in code
- [ ] README updated if adding user-facing features

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
