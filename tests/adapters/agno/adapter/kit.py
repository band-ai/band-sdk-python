from __future__ import annotations

from datetime import UTC, datetime

from band.core.types import PlatformMessage


def _msg(
    room_id: str,
    content: str,
    *,
    msg_id: str = "m1",
    sender_id: str = "user-1",
) -> PlatformMessage:
    """A minimal PlatformMessage for driving on_message in a given room."""
    return PlatformMessage(
        id=msg_id,
        room_id=room_id,
        content=content,
        sender_id=sender_id,
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )
