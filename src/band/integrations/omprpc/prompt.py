"""In-flight native OMP prompt state."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum

from band.integrations.omprpc.errors import BACKLOG_EXCEEDED, OmpProcessError
from band.integrations.ompwire import (
    OMP_ASSISTANT_ROLE,
    OmpEvent,
    OmpEventType,
    OmpHostToolCall,
    OmpMessage,
    OmpPromptResult,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)

EVENT_BACKLOG_LIMIT = 256


class OmpCallbackKind(StrEnum):
    TOOL = "tool"
    UI = "ui"


class PromptRun:
    def __init__(
        self,
        request_id: str,
        on_event: Callable[[OmpEvent], Awaitable[None]],
        on_tool: Callable[[OmpHostToolCall], Awaitable[OmpToolResult]],
        on_ui: Callable[[OmpUIRequest], Awaitable[OmpUIResponse]],
    ) -> None:
        loop = asyncio.get_running_loop()
        self.id = request_id
        self.on_event = on_event
        self.on_tool = on_tool
        self.on_ui = on_ui
        self.events: asyncio.Queue[OmpEvent] = asyncio.Queue(
            maxsize=EVENT_BACKLOG_LIMIT
        )
        self.failure: asyncio.Future[BaseException] = loop.create_future()
        self.result: asyncio.Future[OmpPromptResult] = loop.create_future()
        self.changed = asyncio.Event()
        self.last_activity_sequence = 0
        self.settled_sequence = 0
        self.last_assistant: OmpMessage | None = None
        self.rejected = False
        self.callbacks: dict[tuple[OmpCallbackKind, str], asyncio.Task[None]] = {}
        self.callback_tasks: set[asyncio.Task[None]] = set()
        self.event_worker: asyncio.Task[None] | None = None

    def fail(self, error: BaseException) -> None:
        if not self.failure.done():
            self.failure.set_result(error)

    def observe(self, event: OmpEvent, sequence: int) -> OmpProcessError | None:
        if self.failure.done():
            return None
        # Waiting for space would block the reader that services reentrant commands.
        try:
            self.events.put_nowait(event)
        except asyncio.QueueFull:
            error = OmpProcessError(BACKLOG_EXCEEDED)
            self.fail(error)
            return error
        self.last_activity_sequence = sequence
        match event.type:
            case OmpEventType.MESSAGE_END if (
                event.message is not None and event.message.role == OMP_ASSISTANT_ROLE
            ):
                self.last_assistant = event.message
            case OmpEventType.AGENT_END:
                self.last_assistant = next(
                    (
                        message
                        for message in reversed(event.messages)
                        if message.role == OMP_ASSISTANT_ROLE
                    ),
                    self.last_assistant,
                )
        self.changed.set()
        return None
