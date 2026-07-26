"""Types for A2A Gateway adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from a2a.server.events import EventQueue
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
        event_queue: Official A2A event queue owned by DefaultRequestHandler.
        peer_id: The target peer this request is for.
        done: Set when the final Band reply has been emitted or the room is
            cleaned up.
    """

    task: Task
    event_queue: EventQueue
    peer_id: str
    done: asyncio.Event

    async def publish_response(self, event: TaskStatusUpdateEvent) -> None:
        """Publish a response and release the executor on terminal events."""
        await self.event_queue.enqueue_event(event)
        if event.final:
            self.done.set()
