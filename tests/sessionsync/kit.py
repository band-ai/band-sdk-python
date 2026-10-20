from __future__ import annotations

from datetime import UTC, datetime

from band.runtime.messages import PlatformMessage


def make_message(msg_id: str, room_id: str = "room-123") -> PlatformMessage:
    """Helper to create test messages for /next API simulation."""
    return PlatformMessage(
        id=msg_id,
        room_id=room_id,
        content=f"Content for {msg_id}",
        sender_id="user-456",
        sender_type="User",
        sender_name="Test User",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )
