"""Kit: the resume record and history entries the session resume tests share."""

from __future__ import annotations

from typing import Any

from band.core.types import MessageType
from band.runtime.sessionresume import ResumeRecord, SessionSeed

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
