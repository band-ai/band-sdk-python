"""Seed helpers shared across FakeAgentTools-backed test suites."""

from __future__ import annotations

from typing import Any

SEED_INSERTED_AT = "2025-01-01T00:00:00Z"


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


def seeded_peer(
    id: str, *, handle: str, name: str, type: str = "User"
) -> dict[str, Any]:
    """A minimal valid ``Peer`` seed for ``FakeAgentTools(peers=...)``."""
    return {
        "id": id,
        "handle": handle,
        "name": name,
        "type": type,
        "is_contact": False,
        "source": "registry",
        "online": True,
    }


def seeded_contact(
    id: str, *, handle: str, name: str, type: str = "User"
) -> dict[str, Any]:
    """A minimal valid ``AgentContact`` seed for ``FakeAgentTools(contacts=...)``."""
    return {
        "id": id,
        "handle": handle,
        "name": name,
        "type": type,
        "inserted_at": SEED_INSERTED_AT,
        "online": True,
    }
