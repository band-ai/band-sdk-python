"""A remote A2A agent for adapter tests: scripted streams, recorded cancels."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from a2a.helpers import new_text_message
from a2a.types import (
    CancelTaskRequest,
    Role,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
)

CANCELLED = "tasks/cancel"
WORKING = TaskState.Name(TaskState.TASK_STATE_WORKING)
CANCELED = TaskState.Name(TaskState.TASK_STATE_CANCELED)


def task_update(
    task_id: str,
    state_name: str,
    *,
    context_id: str = "ctx-1",
    text: str | None = None,
) -> StreamResponse:
    """A stream event reporting ``task_id`` in the named ``TaskState``, with
    ``text`` as the agent's status message."""
    status = TaskStatus(
        state=TaskState.Value(state_name),
        message=new_text_message(text, role=Role.ROLE_AGENT) if text else None,
    )
    return StreamResponse(task=Task(id=task_id, context_id=context_id, status=status))


@dataclass(frozen=True)
class ScriptedStream:
    """One reply stream: these events, then either the end or a held turn.

    A ``gate`` keeps the remote silent after it accepted the request, until
    the gate is set.
    """

    events: tuple[StreamResponse, ...]
    hold: bool = False
    gate: asyncio.Event | None = None


class RecordingA2AClient:
    """An A2A client whose remote agent follows scripted streams.

    It records the task each request continues and each task it is asked to
    cancel.
    """

    def __init__(self) -> None:
        self.continued: list[str] = []
        self.cancelled: list[str] = []
        self.closed = False
        self.cancel_started = asyncio.Event()
        self._cancel_release: asyncio.Event | None = None
        self._cancelled_ids: set[str] = set()
        self._streams: deque[ScriptedStream] = deque()

    def hold_cancel(self) -> asyncio.Event:
        """``cancel_task`` waits for the returned event after it has started."""
        self._cancel_release = asyncio.Event()
        return self._cancel_release

    def script(
        self,
        *events: StreamResponse,
        hold: bool = False,
        gate: asyncio.Event | None = None,
    ) -> None:
        self._streams.append(ScriptedStream(events, hold, gate))

    async def send_message(
        self, request: SendMessageRequest, *, context: Any = None
    ) -> AsyncIterator[StreamResponse]:
        self.continued.append(request.message.task_id)
        stream = self._streams.popleft()
        if stream.gate is not None:
            await stream.gate.wait()
        for event in stream.events:
            yield event
        if stream.hold:
            await asyncio.Future[None]()

    async def cancel_task(
        self, request: CancelTaskRequest, *, context: Any = None
    ) -> Task:
        self.cancel_started.set()
        if self._cancel_release is not None:
            await self._cancel_release.wait()
        self._cancelled_ids.add(request.id)
        self.cancelled.append(f"{CANCELLED} {request.id}")
        return Task(
            id=request.id, status=TaskStatus(state=TaskState.TASK_STATE_CANCELED)
        )

    async def subscribe(
        self, request: Any, *, context: Any = None
    ) -> AsyncIterator[StreamResponse]:
        """The task stays non-terminal until its cancel has finished."""
        state = CANCELED if request.id in self._cancelled_ids else WORKING
        yield task_update(request.id, state)

    async def close(self) -> None:
        self.closed = True
