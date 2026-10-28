"""Kit: the resume record and history entries the session resume tests share."""

from __future__ import annotations

from typing import Any

from band.core.types import MessageType
from band.runtime.sessionresume import ResumeRecord, SessionSeed
from band.testing import FakeAgentTools

RECORD = ResumeRecord(
    label="Test session", room_key="test_room_id", session_key="test_session_id"
)


def record(
    room: object, *, message_type: str = MessageType.TASK, **metadata: object
) -> dict[str, Any]:
    """A history entry carrying ``metadata`` under ``RECORD``'s room key."""
    return {
        "message_type": message_type,
        "content": RECORD.label,
        "metadata": {RECORD.room_key: room, **metadata},
    }


def session(value: object) -> dict[str, Any]:
    """The session field of a ``RECORD`` entry."""
    return {RECORD.session_key: value}


def confirmed(seed: SessionSeed) -> SessionSeed:
    """``seed`` after its seeding prompt completed."""
    seed.confirm()
    return seed


def stored_text(index: int, content: str, sender: str = "user-1") -> dict[str, Any]:
    """A room-context text message as the platform stores it."""
    return {
        "id": f"m-{index}",
        "content": content,
        "sender_id": sender,
        "sender_type": "User",
        "sender_name": "Alice",
        "message_type": MessageType.TEXT,
        "inserted_at": "2026-01-01T00:00:00Z",
    }


class UncountedContext(FakeAgentTools):
    """Room context whose pages report no page count."""

    async def fetch_room_context(self, **kwargs: Any) -> dict[str, Any]:
        context = await super().fetch_room_context(**kwargs)
        context["meta"]["total_pages"] = None
        return context


class UnreachableContext(FakeAgentTools):
    """Room context the platform cannot serve."""

    async def fetch_room_context(self, **kwargs: Any) -> dict[str, Any]:
        raise ConnectionError("Band REST unavailable")
