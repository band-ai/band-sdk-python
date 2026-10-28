"""Room history entries as the platform stores and serves them."""

from __future__ import annotations

from typing import Any

from band.core.types import MessageType


def stored(
    content: str, *, message_id: str = "earlier", sender_name: str = "Alice"
) -> dict[str, Any]:
    """One room text message from a person, as the platform stores it."""
    return {
        "id": message_id,
        "message_type": MessageType.TEXT,
        "sender_id": f"id-{sender_name}",
        "sender_type": "User",
        "sender_name": sender_name,
        "content": content,
    }
