"""Why a WebSocket session ended for good."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from band_sdk_core import DeadReason, SessionOutcome, StaleReason

from band.client.streaming.errors import WebSocketUpgradeError


@dataclass(frozen=True)
class WebSocketDisconnectReason:
    """Terminal WebSocket disconnect reason reported by the platform."""

    reason: str
    message: str
    retryable: bool
    retry_after: int | None = None
    target_socket_id: str | None = None
    correlation_id: str | None = None
    dead_reason: DeadReason | None = None
    stale_reason: StaleReason | None = None


class TerminalReason(StrEnum):
    """The ``reason`` of a verdict the SDK reaches without a platform payload."""

    CONNECTION_FAILED = "connection_failed"
    SOCKET_CLOSED = "socket_closed"
    RAPID_DISCONNECT = "rapid_disconnect"
    STALE_CONNECTION = "stale_connection"


def http_refusal_reason(status_code: int) -> str:
    """Name a refusal whose body carries no platform error code."""
    return f"http_{status_code}"


def with_outcome(
    reason: WebSocketDisconnectReason, outcome: SessionOutcome
) -> WebSocketDisconnectReason:
    """Add why the session itself ended (dead or stale) to ``reason``."""
    return replace(
        reason, dead_reason=outcome.dead_reason, stale_reason=outcome.stale_reason
    )


def refusal_reason(
    refusal: WebSocketUpgradeError, outcome: SessionOutcome
) -> WebSocketDisconnectReason:
    """Describe a session ended by a refused upgrade."""
    reason = WebSocketDisconnectReason(
        reason=refusal.code or http_refusal_reason(refusal.status_code),
        message=refusal.message,
        retryable=False,
        retry_after=refusal.retry_after,
        correlation_id=refusal.request_id,
    )
    return with_outcome(reason, outcome)


def session_reason(
    reason: TerminalReason, message: str, outcome: SessionOutcome
) -> WebSocketDisconnectReason:
    """Describe a session ended by a failed connect, a close or a stale socket."""
    return with_outcome(
        WebSocketDisconnectReason(reason=reason, message=message, retryable=False),
        outcome,
    )
