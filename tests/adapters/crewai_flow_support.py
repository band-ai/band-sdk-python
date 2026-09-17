"""Shared seed helpers for the CrewAI flow adapter test suite (phase3-5)."""

from __future__ import annotations

from typing import Any

from tests.testing.support import seeded_participant


def participant_seed(
    id: str, handle: str, name: str | None = None, *, type: str = "Agent"
) -> dict[str, Any]:
    """A minimal valid ``ChatParticipant`` seed for ``FakeAgentTools(participants=...)``,
    defaulting to an Agent since these flows are agent-to-agent."""
    return seeded_participant(id, handle=handle, name=name, type=type)
