"""Shared seed helpers for the CrewAI flow adapter test suite (phase3-5)."""

from __future__ import annotations

from typing import Any


def _participant(
    id: str, handle: str, name: str | None = None, *, type: str = "Agent"
) -> dict[str, Any]:
    """A minimal valid ``ChatParticipant`` seed for ``FakeAgentTools(participants=...)``."""
    return {
        "id": id,
        "handle": handle,
        "name": name,
        "role": "member",
        "status": "active",
        "type": type,
    }
