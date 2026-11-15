"""REST message data for stale-processing pagination checks."""

from __future__ import annotations

from unittest.mock import MagicMock


def make_stale_message(
    *, id: str, room_id: str, content: str, sender_id: str, sender_name: str
) -> MagicMock:
    """A fake REST `processing`-status message item, shaped like the
    band_rest SDK's response model just enough for
    ``get_stale_processing_messages`` to project it into a ``PlatformMessage``."""
    msg = MagicMock()
    msg.id = id
    msg.chat_room_id = room_id
    msg.content = content
    msg.sender_id = sender_id
    msg.sender_type = "User"
    msg.sender_name = sender_name
    msg.message_type = "text"
    msg.metadata = {}
    msg.inserted_at = None
    return msg
