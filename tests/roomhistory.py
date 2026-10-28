"""Room history entries as the platform stores and serves them."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from band.core.types import MessageType, PlatformMessage

# Default insertion time for historical room fixtures.
DEFAULT_HISTORY_INSERTED_AT = "2023-01-01T00:00:00Z"


def stored(
    content: str,
    *,
    message_id: str = "earlier",
    sender_name: str = "Alice",
    sender_type: str = "User",
    inserted_at: datetime | str | None = DEFAULT_HISTORY_INSERTED_AT,
) -> dict[str, Any]:
    """One room text message, as the platform stores it."""
    return {
        "id": message_id,
        "message_type": MessageType.TEXT,
        "sender_id": f"id-{sender_name}",
        "sender_type": sender_type,
        "sender_name": sender_name,
        "content": content,
        **({"inserted_at": inserted_at} if inserted_at is not None else {}),
    }


def original(message_id: str, created_at: datetime) -> PlatformMessage:
    """Build a current input with an explicit insertion position."""
    return PlatformMessage(
        id=message_id,
        room_id="room-1",
        content="current input",
        sender_id="id-Alice",
        sender_type="User",
        sender_name="Alice",
        message_type=MessageType.TEXT,
        metadata={},
        created_at=created_at,
    )
