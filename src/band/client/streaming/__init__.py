"""Band WebSocket streaming SDK.

This module provides WebSocket-based real-time communication with the Band platform.

Usage:
    from band.client.streaming import WebSocketClient
"""

from band.client.streaming.client import (
    AgentControlPayload,
    ContactAddedPayload,
    ContactRemovedPayload,
    ContactRequestReceivedPayload,
    ContactRequestUpdatedPayload,
    ControlMode,
    DeliveryStatus,
    Mention,
    MessageCreatedPayload,
    MessageMetadata,
    ParticipantAddedPayload,
    ParticipantRemovedPayload,
    RoomAddedPayload,
    RoomDeletedPayload,
    RoomRemovedPayload,
    SupersedePayload,
    WebSocketClient,
    WebSocketDisconnectReason,
    WireEvent,
)
from band.client.streaming.errors import WebSocketUpgradeError

__all__ = [
    "AgentControlPayload",
    "ContactAddedPayload",
    "ContactRemovedPayload",
    "ContactRequestReceivedPayload",
    "ContactRequestUpdatedPayload",
    "ControlMode",
    "DeliveryStatus",
    "Mention",
    "MessageCreatedPayload",
    "MessageMetadata",
    "ParticipantAddedPayload",
    "ParticipantRemovedPayload",
    "RoomAddedPayload",
    "RoomDeletedPayload",
    "RoomRemovedPayload",
    "SupersedePayload",
    "WebSocketClient",
    "WebSocketDisconnectReason",
    "WebSocketUpgradeError",
    "WireEvent",
]
