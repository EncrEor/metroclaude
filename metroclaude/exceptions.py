"""Exception hierarchy for MetroClaude."""


class MetroClaudeError(Exception):
    """Base exception."""


class ConfigurationError(MetroClaudeError):
    """Invalid or missing configuration."""


class TmuxError(MetroClaudeError):
    """tmux operation failed."""


class SessionError(MetroClaudeError):
    """Session management error."""


class MonitorError(MetroClaudeError):
    """JSONL monitor error."""


class SecurityError(MetroClaudeError):
    """Security violation (auth, rate limit, etc.)."""
