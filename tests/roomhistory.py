"""Room history entries as the platform stores and serves them."""

from __future__ import annotations

from typing import Any

from band.core.types import MessageType


def stored(
    content: str,
    *,
    message_id: str = "earlier",
    sender_name: str = "Alice",
    sender_type: str = "User",
) -> dict[str, Any]:
    """One room text message, as the platform stores it."""
    return {
        "id": message_id,
        "message_type": MessageType.TEXT,
        "sender_id": f"id-{sender_name}",
        "sender_type": sender_type,
        "sender_name": sender_name,
        "content": content,
    }
