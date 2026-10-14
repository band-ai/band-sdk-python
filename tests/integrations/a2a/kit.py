"""A remote A2A agent for adapter tests: scripted streams, recorded cancels."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from a2a.types import (
    CancelTaskRequest,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
)

CANCELLED = "tasks/cancel"


def task_update(
    task_id: str, state_name: str, *, context_id: str = "ctx-1"
) -> StreamResponse:
    """A stream event reporting ``task_id`` in the named ``TaskState``."""
    status = TaskStatus(state=TaskState.Value(state_name))
    return StreamResponse(task=Task(id=task_id, context_id=context_id, status=status))


@dataclass(frozen=True)
class ScriptedStream:
    """One reply stream: these events, then either the end or a held turn."""

    events: tuple[StreamResponse, ...]
    hold: bool = False


class RecordingA2AClient:
    """An A2A client whose remote agent follows scripted streams.

    It records the task each request continues and each task it is asked to
    cancel.
    """

    def __init__(self) -> None:
        self.continued: list[str] = []
        self.cancelled: list[str] = []
        self.closed = False
        self._streams: deque[ScriptedStream] = deque()

    def script(self, *events: StreamResponse, hold: bool = False) -> None:
        self._streams.append(ScriptedStream(events, hold))

    async def send_message(
        self, request: SendMessageRequest, *, context: Any = None
    ) -> AsyncIterator[StreamResponse]:
        self.continued.append(request.message.task_id)
        stream = self._streams.popleft()
        for event in stream.events:
            yield event
        if stream.hold:
            await asyncio.Future[None]()

    async def cancel_task(
        self, request: CancelTaskRequest, *, context: Any = None
    ) -> Task:
        self.cancelled.append(f"{CANCELLED} {request.id}")
        return Task(
            id=request.id, status=TaskStatus(state=TaskState.TASK_STATE_CANCELED)
        )

    async def close(self) -> None:
        self.closed = True
