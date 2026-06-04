"""Factory helpers for creating test events.

Provides functions for creating event objects used in WebSocket
message handling tests.

Usage:
    from band_testing.factories.events import (
        make_message_event,
        make_room_added_event,
        make_participant_added_event,
    )

    # Create a message event
    event = make_message_event(
        room_id="room-123",
        msg_id="msg-456",
        content="Hello!",
        sender_id="user-789",
        sender_type="User",
    )

    # Use in tests
    await handler.on_message(event)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from band_testing.streaming import (
    MessageCreatedPayload,
    MessageMetadata,
    ParticipantAddedPayload,
    ParticipantRemovedPayload,
    RoomAddedPayload,
    RoomOwner,
    RoomRemovedPayload,
)

# =============================================================================
# Event Wrapper Classes
# =============================================================================
# These are simple dataclasses that wrap payload types.
# They provide a consistent interface for event handlers.


@dataclass
class MessageEvent:
    """Wrapper for message created events."""

    type: Literal["message_created"] = "message_created"
    room_id: str | None = None
    payload: MessageCreatedPayload | None = None
    raw: dict[str, Any] | None = None


@dataclass
class RoomAddedEvent:
    """Wrapper for room added events."""

    type: Literal["room_added"] = "room_added"
    room_id: str | None = None
    payload: RoomAddedPayload | None = None
    raw: dict[str, Any] | None = None


@dataclass
class RoomRemovedEvent:
    """Wrapper for room removed events."""

    type: Literal["room_removed"] = "room_removed"
    room_id: str | None = None
    payload: RoomRemovedPayload | None = None
    raw: dict[str, Any] | None = None


@dataclass
class ParticipantAddedEvent:
    """Wrapper for participant added events."""

    type: Literal["participant_added"] = "participant_added"
    room_id: str | None = None
    payload: ParticipantAddedPayload | None = None
    raw: dict[str, Any] | None = None


@dataclass
class ParticipantRemovedEvent:
    """Wrapper for participant removed events."""

    type: Literal["participant_removed"] = "participant_removed"
    room_id: str | None = None
    payload: ParticipantRemovedPayload | None = None
    raw: dict[str, Any] | None = None


# =============================================================================
# Factory Functions
# =============================================================================


def make_message_event(
    room_id: str = "room-123",
    msg_id: str = "msg-123",
    content: str = "Test message",
    sender_id: str = "user-456",
    sender_type: str = "User",
    **kwargs: Any,
) -> MessageEvent:
    """Create a MessageEvent for tests.

    Args:
        room_id: Chat room ID.
        msg_id: Message ID.
        content: Message content.
        sender_id: Sender's ID.
        sender_type: Sender type ("User" or "Agent").
        **kwargs: Additional payload fields:
            - message_type: Type of message (default: "text")
            - inserted_at: Timestamp (default: "2024-01-01T00:00:00Z")
            - updated_at: Timestamp (default: "2024-01-01T00:00:00Z")
            - metadata: MessageMetadata object or None

    Returns:
        MessageEvent with the specified payload.
    """
    metadata = kwargs.get("metadata")
    if metadata is None:
        metadata = MessageMetadata(mentions=[], status="sent")

    payload = MessageCreatedPayload(
        id=msg_id,
        content=content,
        message_type=kwargs.get("message_type", "text"),
        sender_id=sender_id,
        sender_type=sender_type,
        chat_room_id=room_id,
        inserted_at=kwargs.get("inserted_at", "2024-01-01T00:00:00Z"),
        updated_at=kwargs.get("updated_at", "2024-01-01T00:00:00Z"),
        metadata=metadata,
    )
    return MessageEvent(room_id=room_id, payload=payload)


def make_room_added_event(
    room_id: str = "room-123",
    title: str = "Test Room",
    **kwargs: Any,
) -> RoomAddedEvent:
    """Create a RoomAddedEvent for tests.

    Args:
        room_id: Chat room ID.
        title: Room title.
        **kwargs: Additional payload fields:
            - owner: RoomOwner object
            - status: Room status (default: "active")
            - type: Room type (default: "direct")
            - created_at: Timestamp
            - participant_role: Role (default: "member")

    Returns:
        RoomAddedEvent with the specified payload.
    """
    owner = kwargs.get("owner", RoomOwner(id="user-1", name="Test User", type="User"))

    payload = RoomAddedPayload(
        id=room_id,
        title=title,
        owner=owner,
        status=kwargs.get("status", "active"),
        type=kwargs.get("type", "direct"),
        created_at=kwargs.get("created_at", "2024-01-01T00:00:00Z"),
        participant_role=kwargs.get("participant_role", "member"),
    )
    return RoomAddedEvent(room_id=room_id, payload=payload)


def make_room_removed_event(
    room_id: str = "room-123",
    title: str = "Test Room",
    **kwargs: Any,
) -> RoomRemovedEvent:
    """Create a RoomRemovedEvent for tests.

    Args:
        room_id: Chat room ID.
        title: Room title.
        **kwargs: Additional payload fields:
            - status: Status (default: "removed")
            - type: Room type (default: "direct")
            - removed_at: Timestamp

    Returns:
        RoomRemovedEvent with the specified payload.
    """
    payload = RoomRemovedPayload(
        id=room_id,
        status=kwargs.get("status", "removed"),
        type=kwargs.get("type", "direct"),
        title=title,
        removed_at=kwargs.get("removed_at", "2024-01-01T00:00:00Z"),
    )
    return RoomRemovedEvent(room_id=room_id, payload=payload)


def make_participant_added_event(
    room_id: str = "room-123",
    participant_id: str = "user-456",
    name: str = "Test User",
    type: str = "User",
) -> ParticipantAddedEvent:
    """Create a ParticipantAddedEvent for tests.

    Args:
        room_id: Chat room ID.
        participant_id: Participant's ID.
        name: Participant's name.
        type: Participant type ("User" or "Agent").

    Returns:
        ParticipantAddedEvent with the specified payload.
    """
    payload = ParticipantAddedPayload(id=participant_id, name=name, type=type)
    return ParticipantAddedEvent(room_id=room_id, payload=payload)


def make_participant_removed_event(
    room_id: str = "room-123",
    participant_id: str = "user-456",
) -> ParticipantRemovedEvent:
    """Create a ParticipantRemovedEvent for tests.

    Args:
        room_id: Chat room ID.
        participant_id: ID of the removed participant.

    Returns:
        ParticipantRemovedEvent with the specified payload.
    """
    payload = ParticipantRemovedPayload(id=participant_id)
    return ParticipantRemovedEvent(room_id=room_id, payload=payload)
