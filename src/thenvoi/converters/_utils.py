"""Shared converter utilities."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from thenvoi.core.types import is_text_message_type


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


def format_replay_message(message: dict[str, Any]) -> str | None:
    """Render a persisted room message as plain replay context."""
    message_type = str(message.get("message_type") or "text")
    content = optional_str(message.get("content"))
    if not content:
        return None

    if is_text_message_type(message_type):
        sender_name = (
            optional_str(message.get("sender_name"))
            or optional_str(message.get("sender_type"))
            or "Unknown"
        )
        return f"[{sender_name}]: {content}"
    if message_type == "tool_call":
        return f"[Tool Call]: {content}"
    if message_type == "tool_result":
        return f"[Tool Result]: {content}"
    return None


def build_replay_messages(raw: list[dict[str, Any]]) -> list[str]:
    """Build plain replay context lines without dropping completed tool history."""
    return [line for message in raw if (line := format_replay_message(message))]
