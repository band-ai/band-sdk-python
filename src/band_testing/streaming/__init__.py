"""Streaming payload types for WebSocket events.

This module provides Pydantic models for WebSocket event payloads.
These are the same types used by band-sdk-python's streaming module.

Usage:
    from band_testing.streaming import (
        MessageCreatedPayload,
        RoomAddedPayload,
        MessageMetadata,
        Mention,
    )

    # Create a message payload
    payload = MessageCreatedPayload(
        id="msg-123",
        content="Hello!",
        message_type="text",
        metadata=MessageMetadata(mentions=[], status="sent"),
        sender_id="user-456",
        sender_type="User",
        chat_room_id="room-789",
        inserted_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Mention(BaseModel):
    """Mention object within message metadata."""

    id: str
    username: str


class MessageMetadata(BaseModel):
    """Metadata within message_created payload."""

    mentions: list[Mention]
    status: str


class MessageCreatedPayload(BaseModel):
    """Payload for message_created events."""

    model_config = ConfigDict(extra="allow")

    id: str
    content: str
    message_type: str
    metadata: MessageMetadata
    sender_id: str
    sender_type: str
    chat_room_id: str
    thread_id: str | None = None
    inserted_at: str
    updated_at: str


class RoomOwner(BaseModel):
    """Owner object within room_added payload."""

    id: str
    name: str
    type: str


class RoomAddedPayload(BaseModel):
    """Payload for room_added events."""

    model_config = ConfigDict(extra="allow")

    id: str
    owner: RoomOwner
    status: str
    type: str
    title: str
    created_at: str
    participant_role: str


class RoomRemovedPayload(BaseModel):
    """Payload for room_removed events."""

    model_config = ConfigDict(extra="allow")

    id: str
    status: str
    type: str
    title: str
    removed_at: str


class ParticipantAddedPayload(BaseModel):
    """Payload for participant_added events."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    type: str


class ParticipantRemovedPayload(BaseModel):
    """Payload for participant_removed events."""

    model_config = ConfigDict(extra="allow")

    id: str


__all__ = [
    "Mention",
    "MessageMetadata",
    "MessageCreatedPayload",
    "RoomOwner",
    "RoomAddedPayload",
    "RoomRemovedPayload",
    "ParticipantAddedPayload",
    "ParticipantRemovedPayload",
]
