from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

CONNECTION_CONFLICT_CODE = "connection_conflict"


class WebSocketUpgradeError(Exception):
    """HTTP error returned while upgrading the WebSocket connection."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str | None = None,
        message: str | None = None,
        request_id: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message or f"WebSocket upgrade failed with HTTP {status_code}"
        self.request_id = request_id
        self.retry_after = retry_after
        super().__init__(self.message)

    @property
    def is_connection_conflict(self) -> bool:
        return self.code == CONNECTION_CONFLICT_CODE

    @classmethod
    def from_exception(
        cls, exc: Exception, *, redact: Callable[[str], str] = str
    ) -> WebSocketUpgradeError | None:
        """Parse a websockets handshake exception when it exposes the HTTP response."""
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code is None:
            status_code = getattr(exc, "status_code", None)
        if not isinstance(status_code, int) or isinstance(status_code, bool):
            return None

        headers = getattr(response, "headers", None) or getattr(exc, "headers", None)
        body = getattr(response, "body", b"") if response is not None else b""
        payload = _decode_upgrade_error_body(body)
        error = payload.get("error")
        if not isinstance(error, dict):
            error = {}

        code = error.get("code")
        message = error.get("message")
        request_id = error.get("request_id")
        retry_after = _parse_retry_after(error.get("retry_after"))
        if retry_after is None and headers is not None:
            retry_after = _parse_retry_after(_get_header(headers, "Retry-After"))

        return cls(
            status_code=status_code,
            code=redact(code) if isinstance(code, str) else None,
            message=redact(message) if isinstance(message, str) else None,
            request_id=redact(request_id) if isinstance(request_id, str) else None,
            retry_after=retry_after,
        )


def _decode_upgrade_error_body(body: Any) -> dict[str, Any]:
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", errors="replace")
    if not isinstance(body, str) or not body.strip():
        return {}
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _parse_retry_after(value: Any) -> int | None:
    """Read a whole-second retry hint; anything else, negatives included, is no hint."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _get_header(headers: Any, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value is None:
            value = getter(name.lower())
        return value if isinstance(value, str) else None
    return None
