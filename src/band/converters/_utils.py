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
