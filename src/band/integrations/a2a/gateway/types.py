"""Types for A2A Gateway adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from a2a.types import Task, TaskStatusUpdateEvent


@dataclass
class GatewaySessionState:
    """Session state extracted from platform history.

    Used by GatewayHistoryConverter to restore gateway session state
    when the agent rejoins a chat room.

    Attributes:
        context_to_room: Mapping of A2A context_id to Band room_id.
        room_participants: Mapping of room_id to set of peer_ids in that room.
    """

    context_to_room: dict[str, str] = field(default_factory=dict)
    room_participants: dict[str, set[str]] = field(default_factory=dict)


@dataclass
class PendingA2ATask:
    """Tracks an in-flight A2A request awaiting response.

    When the gateway receives an A2A HTTP request, it creates a PendingA2ATask
    to correlate the eventual response from the Band platform with the
    SSE stream back to the A2A client.

    Attributes:
        task: The A2A Task object tracking this request.
        sse_queue: Queue for streaming TaskStatusUpdateEvent to the client.
        peer_id: The target peer this request is for.
    """

    task: Task
    sse_queue: asyncio.Queue[TaskStatusUpdateEvent]
    peer_id: str
