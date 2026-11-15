"""Original delivery identity and conversion at the runtime boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TypeGuard

from band.client.streaming import MessageCreatedPayload, MessageMetadata
from band.core.types import is_contact_hub_turn, metadata_to_dict
from band.platform.event import MessageEvent, PlatformEvent
from band.runtime.messages import PlatformMessage


def payload(event: MessageEvent) -> MessageCreatedPayload:
    """The payload of a message event that carries a real delivery."""
    if event.payload is None:
        raise ValueError("a message event without a payload has no delivery")
    return event.payload


def message_id(event: MessageEvent) -> str:
    """The platform id of a message event that carries a real delivery."""
    return payload(event).id


def delivery_id(event: PlatformEvent) -> str | None:
    """The platform id of a message delivery; ``None`` for any other event."""
    if isinstance(event, MessageEvent) and event.payload is not None:
        return event.payload.id
    return None


def is_ordinary(event: PlatformEvent) -> TypeGuard[MessageEvent]:
    """Whether the event is a real delivery a turn can include."""
    return delivery_id(event) is not None and not is_synthetic(event)


def is_synthetic(event: PlatformEvent) -> TypeGuard[MessageEvent]:
    """Whether the event is a contact-hub turn that owes no delivery write."""
    return (
        isinstance(event, MessageEvent)
        and event.payload is not None
        and is_contact_hub_turn(
            sender_type=event.payload.sender_type, sender_id=event.payload.sender_id
        )
    )


def backlog_event(msg: PlatformMessage) -> MessageEvent:
    created_at_str = (
        msg.created_at.isoformat() if msg.created_at else datetime.now(UTC).isoformat()
    )

    # REST mentions may use name; the WebSocket projection requires username.
    metadata = metadata_to_dict(msg.metadata).copy()
    if "mentions" in metadata:
        normalized_mentions = []
        for mention in metadata.get("mentions", []):
            if isinstance(mention, dict):
                normalized_mentions.append(
                    {
                        "id": mention.get("id", ""),
                        "username": mention.get("username")
                        or mention.get("name")
                        or mention.get("id", ""),
                    }
                )
        metadata["mentions"] = normalized_mentions
    else:
        metadata["mentions"] = []

    if "status" not in metadata:
        metadata["status"] = "sent"

    return MessageEvent(
        room_id=msg.room_id,
        payload=MessageCreatedPayload(
            id=msg.id,
            content=msg.content,
            sender_id=msg.sender_id,
            sender_type=msg.sender_type,
            message_type=msg.message_type,
            metadata=MessageMetadata(**metadata),
            chat_room_id=msg.room_id,
            inserted_at=created_at_str,
            updated_at=created_at_str,
        ),
    )
