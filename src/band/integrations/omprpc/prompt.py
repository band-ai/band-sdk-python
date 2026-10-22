"""In-flight native OMP prompt state."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import Context, copy_context
from enum import StrEnum

from band.integrations.omprpc.errors import BACKLOG_EXCEEDED, OmpProcessError
from band.integrations.ompwire import (
    OMP_ASSISTANT_ROLE,
    OmpEvent,
    OmpEventType,
    OmpHostToolCall,
    OmpMessage,
    OmpPromptResult,
    OmpState,
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
        self.stop_after_tool_id: str | None = None
        self.stop_task: asyncio.Task[None] | None = None
        self.stopped = False
        # Reader-spawned callbacks must reenter this prompt's occupancy, not start()'s.
        self.context: Context = copy_context()
        self.result_owed = True
        self.terminal_seen = False
        self.awaiting_async = False
        self.events_pending = 0
        self.snapshot: tuple[int, OmpState] | None = None

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
                self.terminal_seen = self.terminal_seen or event.isTerminal is True
                self.awaiting_async = event.awaitingAsyncWork is True
                self.last_assistant = next(
                    (
                        message
                        for message in reversed(event.messages)
                        if message.role == OMP_ASSISTANT_ROLE
                    ),
                    self.last_assistant,
                )
        self.events_pending += 1
        self.changed.set()
        return None

    def record_snapshot(self, sequence: int, state: OmpState) -> None:
        self.snapshot = (sequence, state)

    def settled_proven(self) -> bool:
        if self.settled_sequence > self.last_activity_sequence:
            return True
        if self.snapshot is None:
            return False
        sequence, state = self.snapshot
        # A sticky event claim is superseded by a newer snapshot that is both
        # settled and positively idle; it gates guard arming, not snapshot proof.
        return (
            state.isSettled
            and idle_flags(state)
            and sequence >= self.last_activity_sequence
        )

    def boundary_reached(self) -> bool:
        return self.result.done() or self.terminal_seen or self.settled_proven()

    def completion_evidence_complete(self) -> bool:
        if self.result_owed and not self.result.done():
            return False
        if not self.settled_proven():
            return False
        return self.events_pending == 0 and all(
            task.done() for task in self.callback_tasks
        )

    def busy_proven(self) -> bool:
        if self.awaiting_async:
            return True
        if self.snapshot is None or self.snapshot[0] <= self.last_activity_sequence:
            return False
        _, state = self.snapshot
        return not idle_flags(state)


def idle_flags(state: OmpState) -> bool:
    """Positive idle proof for a snapshot: no busy signal remains set."""
    return bool(
        not state.isStreaming
        and not state.hasPendingAsyncWork
        and not state.isCompacting
        and state.queuedMessageCount == 0
    )
