"""Canonical formatting for platform handles."""

from __future__ import annotations


def normalize_handle(handle: str | None) -> str | None:
    """Return a nonempty handle with its @ prefix, or None."""
    if not handle:
        return None
    return handle if handle.startswith("@") else f"@{handle}"
