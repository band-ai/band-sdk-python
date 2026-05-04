"""
Platform events using tagged union pattern.

Events are strongly typed using discriminated unions, enabling type-safe
pattern matching and automatic type narrowing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# Import payload models from streaming client
from thenvoi.client.streaming import (
    MessageCreatedPayload,
    RoomAddedPayload,
    RoomRemovedPayload,
    RoomDeletedPayload,
    ParticipantAddedPayload,
    ParticipantRemovedPayload,
    ContactRequestReceivedPayload,
    ContactRequestUpdatedPayload,
    ContactAddedPayload,
    ContactRemovedPayload,
)


@dataclass
class MessageEvent:
    """Message created event."""

    type: Literal["message_created"] = "message_created"
    room_id: str | None = None
    payload: MessageCreatedPayload | None = None
    raw: dict[str, Any] | None = None


@dataclass
class RoomAddedEvent:
    """Room added event."""

    type: Literal["room_added"] = "room_added"
    room_id: str | None = None
    payload: RoomAddedPayload | None = None
    raw: dict[str, Any] | None = None


@dataclass
class RoomRemovedEvent:
    """Room removed event."""

    type: Literal["room_removed"] = "room_removed"
    room_id: str | None = None
    payload: RoomRemovedPayload | None = None
    raw: dict[str, Any] | None = None


@dataclass
class RoomDeletedEvent:
    """Room deleted event."""

    type: Literal["room_deleted"] = "room_deleted"
    room_id: str | None = None
    payload: RoomDeletedPayload | None = None
    raw: dict[str, Any] | None = None


@dataclass
class ParticipantAddedEvent:
    """Participant added event."""

    type: Literal["participant_added"] = "participant_added"
    room_id: str | None = None
    payload: ParticipantAddedPayload | None = None
    raw: dict[str, Any] | None = None


@dataclass
class ParticipantRemovedEvent:
    """Participant removed event."""

    type: Literal["participant_removed"] = "participant_removed"
    room_id: str | None = None
    payload: ParticipantRemovedPayload | None = None
    raw: dict[str, Any] | None = None


@dataclass
class ContactRequestReceivedEvent:
    """Contact request received event."""

    type: Literal["contact_request_received"] = "contact_request_received"
    room_id: str | None = None  # Always None for contact events
    payload: ContactRequestReceivedPayload | None = None
    raw: dict[str, Any] | None = None


@dataclass
class ContactRequestUpdatedEvent:
    """Contact request updated event."""

    type: Literal["contact_request_updated"] = "contact_request_updated"
    room_id: str | None = None
    payload: ContactRequestUpdatedPayload | None = None
    raw: dict[str, Any] | None = None


@dataclass
class ContactAddedEvent:
    """Contact added event."""

    type: Literal["contact_added"] = "contact_added"
    room_id: str | None = None
    payload: ContactAddedPayload | None = None
    raw: dict[str, Any] | None = None


@dataclass
class ContactRemovedEvent:
    """Contact removed event."""

    type: Literal["contact_removed"] = "contact_removed"
    room_id: str | None = None
    payload: ContactRemovedPayload | None = None
    raw: dict[str, Any] | None = None


@dataclass
class ReconnectedEvent:
    """Emitted when the WebSocket reconnects after a disconnection.

    Signals RoomPresence to re-derive room state from the server
    instead of relying on stale subscription state.
    """

    type: Literal["reconnected"] = "reconnected"
    room_id: str | None = None
    payload: None = None
    raw: dict[str, Any] | None = None


# Contact event union (for type narrowing)
ContactEvent = (
    ContactRequestReceivedEvent
    | ContactRequestUpdatedEvent
    | ContactAddedEvent
    | ContactRemovedEvent
)


# Union type for all platform events
PlatformEvent = (
    MessageEvent
    | RoomAddedEvent
    | RoomRemovedEvent
    | RoomDeletedEvent
    | ParticipantAddedEvent
    | ParticipantRemovedEvent
    | ContactRequestReceivedEvent
    | ContactRequestUpdatedEvent
    | ContactAddedEvent
    | ContactRemovedEvent
    | ReconnectedEvent
)
