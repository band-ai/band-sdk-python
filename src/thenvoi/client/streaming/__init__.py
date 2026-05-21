"""Band WebSocket streaming SDK.

This module provides WebSocket-based real-time communication with the Band platform.

Usage:
    from thenvoi.client.streaming import WebSocketClient
"""

from thenvoi.client.streaming.client import (
    WebSocketClient,
    MessageCreatedPayload,
    RoomAddedPayload,
    RoomRemovedPayload,
    RoomDeletedPayload,
    ParticipantAddedPayload,
    ParticipantRemovedPayload,
    MessageMetadata,
    Mention,
    ContactRequestReceivedPayload,
    ContactRequestUpdatedPayload,
    ContactAddedPayload,
    ContactRemovedPayload,
)

__all__ = [
    "WebSocketClient",
    "MessageCreatedPayload",
    "RoomAddedPayload",
    "RoomRemovedPayload",
    "RoomDeletedPayload",
    "ParticipantAddedPayload",
    "ParticipantRemovedPayload",
    "MessageMetadata",
    "Mention",
    "ContactRequestReceivedPayload",
    "ContactRequestUpdatedPayload",
    "ContactAddedPayload",
    "ContactRemovedPayload",
]
