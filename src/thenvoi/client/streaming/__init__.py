"""Thenvoi WebSocket streaming SDK.

This module provides WebSocket-based real-time communication with the Thenvoi platform.

Usage:
    from thenvoi.client.streaming import WebSocketClient
"""

from thenvoi.client.streaming.client import (
    WebSocketClient,
    WebSocketDisconnectReason,
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
    SupersedePayload,
)
from thenvoi.client.streaming.errors import WebSocketUpgradeError

__all__ = [
    "WebSocketClient",
    "WebSocketDisconnectReason",
    "WebSocketUpgradeError",
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
    "SupersedePayload",
]
