"""Types for A2A Gateway adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.helpers import new_text_message
from a2a.types import Message, Task


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
        done: Set when the final Band reply has been emitted or the room is
            cleaned up.
    """

    task: Task
    event_queue: EventQueue
    done: asyncio.Event = field(default_factory=asyncio.Event)
    _updater: TaskUpdater = field(init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._updater = TaskUpdater(
            self.event_queue, self.task.id, self.task.context_id
        )

    async def report_progress(self, content: str) -> None:
        """Publish a non-terminal progress update from Band."""
        async with self._lock:
            if not self.done.is_set():
                await self._updater.start_work(self._message(content))

    async def complete_with_message(self, content: str) -> None:
        """Publish Band's final response and release the request."""
        async with self._lock:
            if self.done.is_set():
                return
            await self._updater.complete(self._message(content))
            self.done.set()

    async def fail(self, reason: str) -> None:
        """Publish a terminal failure and release the request."""
        async with self._lock:
            if self.done.is_set():
                return
            await self._updater.failed(self._message(reason))
            self.done.set()

    async def cancel(self) -> None:
        """Publish a terminal cancellation and release the request."""
        async with self._lock:
            if self.done.is_set():
                return
            await self._updater.cancel()
            self.done.set()

    def _message(self, content: str) -> Message:
        return new_text_message(
            content,
            context_id=self.task.context_id,
            task_id=self.task.id,
        )
