"""Seed helpers shared across FakeAgentTools-backed test suites."""

from __future__ import annotations

from typing import Any


def seeded_participant(
    id: str,
    *,
    handle: str | None = None,
    name: str | None = None,
    role: str = "member",
    status: str = "active",
    type: str = "User",
) -> dict[str, Any]:
    """A minimal valid ``ChatParticipant`` seed for ``FakeAgentTools(participants=...)``."""
    return {
        "id": id,
        "handle": handle,
        "name": name,
        "role": role,
        "status": status,
        "type": type,
    }
