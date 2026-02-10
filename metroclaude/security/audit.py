"""Structured audit logging for security events.

Inspired by RichardAtCT/claude-code-telegram (src/security/audit.py).
Adapted for MetroClaude: single-user tmux bridge, lightweight.

Features:
  - AuditEvent dataclass with risk levels (low/medium/high/critical)
  - In-memory ring buffer with auto-trim
  - JSON-structured log lines for grep/analysis
  - Specialized methods matching MetroClaude security modules

Resolves: P2-SEC6 (structured audit logging), P2-SEC7 (tracking for post-mortem).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class RiskLevel(str, Enum):
    LOW = "low"  # Normal operations (auth success, session events)
    MEDIUM = "medium"  # Suspicious (auth failure, rate limit)
    HIGH = "high"  # Attack indicator (injection, repeated failures)
    CRITICAL = "critical"  # Active attack (reserved for escalation)


class EventType(str, Enum):
    AUTH_SUCCESS = "auth_success"
    AUTH_FAILURE = "auth_failure"
    RATE_LIMIT = "rate_limit"
    TMUX_FLOOD = "tmux_flood"
    INJECTION_DETECTED = "injection_detected"
    INPUT_SANITIZED = "input_sanitized"
    SESSION_CREATE = "session_create"
    SESSION_RESUME = "session_resume"
    SESSION_STOP = "session_stop"


@dataclass
class AuditEvent:
    """A single security audit event."""

    timestamp: str
    event_type: str
    user_id: int
    risk_level: str
    success: bool
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.timestamp,
            "event": self.event_type,
            "user": self.user_id,
            "risk": self.risk_level,
            "ok": self.success,
            **self.details,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------


class AuditLogger:
    """Security audit logger with in-memory ring buffer.

    Usage::

        audit = AuditLogger()
        await audit.log_auth_success(user_id=123, username="ahmed")
        await audit.log_auth_failure(user_id=999, username="unknown")

    All events are:
    1. Stored in memory (ring buffer, auto-trimmed)
    2. Logged as JSON via stdlib logging (INFO for normal, WARNING for risky)
    """

    def __init__(self, max_events: int = 1000) -> None:
        self._events: list[AuditEvent] = []
        self._max_events = max_events

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")

    async def _store(self, event: AuditEvent) -> None:
        """Store event and emit structured log line."""
        self._events.append(event)

        # Auto-trim ring buffer
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events :]

        # Emit structured log (WARNING for medium+, INFO for low)
        line = f"[AUDIT] {event.to_json()}"
        if event.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            logger.warning(line)
        elif event.risk_level == RiskLevel.MEDIUM:
            logger.warning(line)
        else:
            logger.info(line)

    # ------------------------------------------------------------------
    # Authentication events
    # ------------------------------------------------------------------

    async def log_auth_success(
        self,
        user_id: int,
        username: str = "",
    ) -> None:
        """Log successful authentication."""
        await self._store(
            AuditEvent(
                timestamp=self._now(),
                event_type=EventType.AUTH_SUCCESS,
                user_id=user_id,
                risk_level=RiskLevel.LOW,
                success=True,
                details={"username": username},
            )
        )

    async def log_auth_failure(
        self,
        user_id: int,
        username: str = "",
        name: str = "",
    ) -> None:
        """Log failed authentication attempt."""
        await self._store(
            AuditEvent(
                timestamp=self._now(),
                event_type=EventType.AUTH_FAILURE,
                user_id=user_id,
                risk_level=RiskLevel.MEDIUM,
                success=False,
                details={"username": username, "name": name},
            )
        )

    # ------------------------------------------------------------------
    # Rate limiting events
    # ------------------------------------------------------------------

    async def log_rate_limit(
        self,
        user_id: int,
        count: int,
        limit: int,
    ) -> None:
        """Log rate limit exceeded."""
        await self._store(
            AuditEvent(
                timestamp=self._now(),
                event_type=EventType.RATE_LIMIT,
                user_id=user_id,
                risk_level=RiskLevel.MEDIUM,
                success=False,
                details={"count": count, "limit": limit},
            )
        )

    async def log_tmux_flood(
        self,
        user_id: int,
        window_name: str,
        cooldown: float,
    ) -> None:
        """Log tmux flood guard trigger."""
        await self._store(
            AuditEvent(
                timestamp=self._now(),
                event_type=EventType.TMUX_FLOOD,
                user_id=user_id,
                risk_level=RiskLevel.LOW,
                success=False,
                details={"window": window_name, "cooldown_s": round(cooldown, 2)},
            )
        )

    # ------------------------------------------------------------------
    # Input sanitization events
    # ------------------------------------------------------------------

    async def log_injection_detected(
        self,
        user_id: int,
        patterns: list[str],
    ) -> None:
        """Log command injection detection."""
        await self._store(
            AuditEvent(
                timestamp=self._now(),
                event_type=EventType.INJECTION_DETECTED,
                user_id=user_id,
                risk_level=RiskLevel.HIGH,
                success=False,
                details={"patterns": patterns[:3]},
            )
        )

    async def log_input_sanitized(
        self,
        user_id: int,
        original_len: int,
        sanitized_len: int,
    ) -> None:
        """Log input modification by sanitizer."""
        await self._store(
            AuditEvent(
                timestamp=self._now(),
                event_type=EventType.INPUT_SANITIZED,
                user_id=user_id,
                risk_level=RiskLevel.MEDIUM,
                success=True,
                details={
                    "original_len": original_len,
                    "sanitized_len": sanitized_len,
                    "removed": original_len - sanitized_len,
                },
            )
        )

    # ------------------------------------------------------------------
    # Session lifecycle events
    # ------------------------------------------------------------------

    async def log_session_event(
        self,
        user_id: int,
        action: str,
        window_name: str = "",
        working_dir: str = "",
    ) -> None:
        """Log session lifecycle event (create, resume, stop)."""
        event_map = {
            "create": EventType.SESSION_CREATE,
            "resume": EventType.SESSION_RESUME,
            "stop": EventType.SESSION_STOP,
        }
        await self._store(
            AuditEvent(
                timestamp=self._now(),
                event_type=event_map.get(action, action),
                user_id=user_id,
                risk_level=RiskLevel.LOW,
                success=True,
                details={"action": action, "window": window_name, "cwd": working_dir},
            )
        )

    # ------------------------------------------------------------------
    # Query interface
    # ------------------------------------------------------------------

    def get_events(
        self,
        *,
        user_id: int | None = None,
        event_type: str | None = None,
        risk_level: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query stored events with optional filters. Returns newest first."""
        events = self._events

        if user_id is not None:
            events = [e for e in events if e.user_id == user_id]
        if event_type is not None:
            events = [e for e in events if e.event_type == event_type]
        if risk_level is not None:
            events = [e for e in events if e.risk_level == risk_level]

        # Newest first, limited
        return [e.to_dict() for e in reversed(events)][:limit]

    def get_summary(self) -> dict[str, Any]:
        """Get audit summary (event counts by type and risk)."""
        by_type: dict[str, int] = {}
        by_risk: dict[str, int] = {}

        for e in self._events:
            by_type[e.event_type] = by_type.get(e.event_type, 0) + 1
            by_risk[e.risk_level] = by_risk.get(e.risk_level, 0) + 1

        return {
            "total": len(self._events),
            "by_type": by_type,
            "by_risk": by_risk,
        }

    @property
    def event_count(self) -> int:
        return len(self._events)
