"""Band WebSocket streaming SDK.

This module provides WebSocket-based real-time communication with the Band platform.

Usage:
    from band.client.streaming import WebSocketClient
"""

from band.client.streaming.client import (
    WebSocketClient,
    WebSocketDisconnectReason,
    ControlMode,
    DeliveryStatus,
    WireEvent,
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
    AgentControlPayload,
)
from band.client.streaming.errors import WebSocketUpgradeError

__all__ = [
    "WebSocketClient",
    "WebSocketDisconnectReason",
    "WebSocketUpgradeError",
    "ControlMode",
    "DeliveryStatus",
    "WireEvent",
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
    "AgentControlPayload",
]
