"""Shared helper that converts Fern context items to plain dicts.

Used by both ``ExecutionContext.hydrate`` (when loading the session-bootstrap
context cache) and ``AgentTools.fetch_room_context`` (when state-reconstruction
adapters page through the agent context endpoint). Defining the shape in one
place keeps the two callers from drifting apart.
"""

from __future__ import annotations

from typing import Any


def context_item_to_dict(item: Any) -> dict[str, Any]:
    """Serialize a Fern context message model into a plain dict.

    The Fern client returns Pydantic models with snake_case attribute names,
    but a sender_name fallback to ``name`` is necessary because some payloads
    carry the participant's display name in the alternate field. Field access
    uses ``getattr`` with defaults to tolerate older platform responses where
    optional fields are absent.
    """
    sender_name = getattr(item, "sender_name", None) or getattr(item, "name", None)
    return {
        "id": item.id,
        "content": getattr(item, "content", ""),
        "sender_id": getattr(item, "sender_id", ""),
        "sender_type": getattr(item, "sender_type", ""),
        "sender_name": sender_name,
        "message_type": getattr(item, "message_type", "text"),
        "metadata": getattr(item, "metadata", {}),
        "inserted_at": getattr(item, "inserted_at", None),
        "created_at": getattr(item, "inserted_at", None),
    }


__all__ = ["context_item_to_dict"]
