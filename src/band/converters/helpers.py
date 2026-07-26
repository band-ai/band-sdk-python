"""Shared converter utilities."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def optional_str(value: Any) -> str | None:
    """Return ``str(value)`` or ``None`` if *value* is ``None``."""
    if value is None:
        return None
    return str(value)


def parse_iso_datetime(value: Any) -> datetime | None:
    """Parse an ISO-8601 string into a :class:`datetime`, or ``None``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_replay_messages(raw: list[dict[str, Any]]) -> list[str]:
    """Render the room's text history as ``[sender]: content`` lines.

    Used by resume-else-replay adapters to re-seed a fresh backend session
    from platform history when the previous session cannot be resumed.
    Non-text messages (task/tool/thought events) are skipped.
    """
    return [line for msg in raw if (line := _replay_line(msg)) is not None]


def _replay_line(msg: dict[str, Any]) -> str | None:
    """The ``[sender]: content`` replay line for a text message, else None."""
    if msg.get("message_type") != "text":
        return None
    content = optional_str(msg.get("content"))
    if not content or not content.strip():
        return None
    sender = (
        optional_str(msg.get("sender_name"))
        or optional_str(msg.get("sender_type"))
        or "Unknown"
    )
    return f"[{sender}]: {content}"
