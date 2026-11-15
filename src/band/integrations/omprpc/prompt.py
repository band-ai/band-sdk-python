"""In-flight native OMP prompt state."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from band.integrations.omprpc.errors import BACKLOG_EXCEEDED, OmpProcessError
from band.integrations.ompwire import (
    OMP_ASSISTANT_ROLE,
    OmpContent,
    OmpContentKind,
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
from band.runtime.tools import BandTool

EVENT_BACKLOG_LIMIT = 256

NATIVE_BACKLOG_YIELD_GUIDANCE = (
    "If delivery is the only remaining action, end the current model step without "
    "further tool calls so OMP can deliver queued follow-ups or async completions. "
    "This native yield is not a Band decline or a claim that the turn is complete."
)

NATIVE_REFUSED_DECLINE_GUIDANCE = (
    "This turn has not declined. Do not poll this tool or claim completion in "
    "plain text."
)

NATIVE_QUEUED_INPUT_GUIDANCE = (
    "Queued native messages may contain participant input, not just tool results; "
    "their contents cannot be inferred from tool or task inventory."
)

PENDING_ASYNC_WORK_LABEL = "hasPendingAsyncWork=true"
QUEUED_MESSAGE_COUNT_PREFIX = "queuedMessageCount="

SOLE_TOOL_SIBLING_RECOVERY = (
    "Finish the other outstanding tool calls and handle their results before "
    f"calling {BandTool.NO_REPLY} alone."
)
SOLE_TOOL_UNSTRUCTURED_RECOVERY = (
    "There are no sibling tool calls to finish from this message; retry "
    f"{BandTool.NO_REPLY} only as a structured sole tool call when the whole "
    "turn needs no reply."
)
SOLE_TOOL_ID_MISMATCH_RECOVERY = (
    "The assistant message names a different tool call id; call "
    f"{BandTool.NO_REPLY} only as the sole tool when the whole turn needs no reply."
)

RECONSIDER_PENDING_WORK = (
    "Reconsider whether a reply is needed after processing the pending work."
)
RECONSIDER_PENDING_INPUT = (
    "Reconsider whether a reply is needed after processing the pending input."
)
RECONSIDER_PENDING_WORK_AND_INPUT = (
    "Reconsider whether a reply is needed after processing the pending work and input."
)


def queued_message_count_label(count: int) -> str:
    """Wire fragment naming a non-zero native queued-message backlog."""
    return f"{QUEUED_MESSAGE_COUNT_PREFIX}{count}"


def tool_call_id_label(tool_call_id: str) -> str:
    """Wire fragment naming the decline tool-call id under proof."""
    return f"toolCallId={tool_call_id}"


def compose_decline_refusals(backlog: str | None, sole: str | None) -> str | None:
    """Join observed decline refusal texts into one host-tool error."""
    texts = [part for part in (backlog, sole) if part is not None]
    return " ".join(texts) if texts else None


def assistant_tool_calls(message: OmpMessage | None) -> tuple[OmpContent, ...] | None:
    """Return structured TOOL_CALL blocks, or None when content is unstructured."""
    if message is None or isinstance(message.content, str):
        return None
    return tuple(
        block for block in message.content if block.type == OmpContentKind.TOOL_CALL
    )


def is_sole_tool_call(message: OmpMessage | None, tool_call_id: str) -> bool:
    """True when the assistant message proves exactly this host tool call."""
    calls = assistant_tool_calls(message)
    return calls is not None and len(calls) == 1 and calls[0].id == tool_call_id


class OmpCallbackKind(StrEnum):
    TOOL = "tool"
    UI = "ui"


@dataclass
class PromptCallback:
    """Keep request identity independent of scheduler execution tasks."""

    identity: object
    task: asyncio.Task[None] | None = None


class CompletionAction(StrEnum):
    STOP = "stop"
    WAIT_FOR_ACTIVITY = "wait_for_activity"
    REFRESH_SNAPSHOT = "refresh_snapshot"
    WATCH_IDLE_BUDGET = "watch_idle_budget"


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
        self.callbacks: dict[tuple[OmpCallbackKind, str], PromptCallback] = {}
        self.callback_tasks: set[asyncio.Task[None]] = set()
        self.event_worker: asyncio.Task[None] | None = None
        self.stop_after_tool_id: str | None = None
        self.stop_commit: Callable[[], None] | None = None
        self.stop_recheck_backlog = False
        self.stop_task: asyncio.Task[None] | None = None
        self.stopped = False
        self.result_owed = True
        self.terminal_seen = False
        self.awaiting_async = False
        self.snapshot: tuple[int, OmpState] | None = None

    def arm_stop(
        self,
        call_id: str,
        *,
        on_commit: Callable[[], None] | None = None,
        recheck_backlog: bool = False,
    ) -> None:
        if self.stop_after_tool_id is None:
            self.stop_after_tool_id = call_id
            self.stop_commit = on_commit
            self.stop_recheck_backlog = recheck_backlog

    def clear_stop(self, call_id: str, *, cancel_task: bool = True) -> None:
        if self.stop_after_tool_id != call_id:
            return
        self.stop_after_tool_id = None
        self.stop_commit = None
        self.stop_recheck_backlog = False
        if cancel_task and self.stop_task is not None and not self.stop_task.done():
            self.stop_task.cancel()

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
        self.changed.set()
        return None

    def record_snapshot(self, sequence: int, state: OmpState) -> None:
        self.snapshot = (sequence, state)
        self.changed.set()

    def settled_proven(self) -> bool:
        if self.settled_sequence > self.last_activity_sequence:
            return True
        return self.snapshot_proves_settlement()

    def snapshot_proves_settlement(self) -> bool:
        if self.snapshot is None:
            return False
        sequence, state = self.snapshot
        return (
            state.isSettled
            and state_is_idle(state)
            and sequence >= self.last_activity_sequence
        )

    def settlement_confirmed(self) -> bool:
        if self.snapshot is None:
            return False
        return self.snapshot_proves_settlement() or self.settled_sequence > max(
            self.snapshot[0], self.last_activity_sequence
        )

    def snapshot_is_stale(self) -> bool:
        return (
            self.snapshot is None
            or self.snapshot[0] <= self.last_activity_sequence
            or self.snapshot[0] < self.settled_sequence
        )

    def completion_action(self) -> CompletionAction:
        if self.rejected:
            return CompletionAction.STOP
        waiting_for_boundary = self.result_owed and not self.boundary_reached()
        if waiting_for_boundary or self.has_pending_native_work():
            return CompletionAction.WAIT_FOR_ACTIVITY
        if self.snapshot_is_stale():
            return CompletionAction.REFRESH_SNAPSHOT
        return CompletionAction.WATCH_IDLE_BUDGET

    def boundary_reached(self) -> bool:
        return self.result.done() or self.terminal_seen or self.settled_proven()

    def is_single_tool_call(self, tool_call_id: str) -> bool:
        return is_sole_tool_call(self.last_assistant, tool_call_id)

    def has_pending_native_work(self) -> bool:
        event_claims_pending_work = (
            self.awaiting_async and self.settled_sequence <= self.last_activity_sequence
        )
        if self.snapshot is None:
            return event_claims_pending_work
        sequence, state = self.snapshot
        if sequence <= max(self.last_activity_sequence, self.settled_sequence):
            return event_claims_pending_work
        if not state_is_idle(state):
            return True
        return event_claims_pending_work and not state.isSettled


def state_has_async_backlog(state: OmpState) -> bool:
    """Native async work or queued messages still need a continuation."""
    return bool(state.hasPendingAsyncWork or state.queuedMessageCount > 0)


def _backlog_reconsider_clause(*, has_work: bool, has_input: bool) -> str:
    match (has_work, has_input):
        case (True, True):
            return RECONSIDER_PENDING_WORK_AND_INPUT
        case (True, False):
            return RECONSIDER_PENDING_WORK
        case (False, True):
            return RECONSIDER_PENDING_INPUT
        case _:
            raise ValueError("backlog reconsider requires at least one blocker")


@dataclass(frozen=True)
class _BacklogObservation:
    blockers: tuple[str, ...]
    recovery: tuple[str, ...]
    has_work: bool
    has_input: bool


def _observe_backlog(state: OmpState) -> _BacklogObservation | None:
    """Collect named backlog blockers and recovery steps, or None when idle."""
    if not state_has_async_backlog(state):
        return None
    has_work = state.hasPendingAsyncWork
    has_input = state.queuedMessageCount > 0
    blockers: list[str] = []
    recovery: list[str] = []
    if has_work:
        blockers.append(PENDING_ASYNC_WORK_LABEL)
        recovery.append("Wait for the existing async work and handle its completion.")
    if has_input:
        blockers.append(queued_message_count_label(state.queuedMessageCount))
        recovery.append(
            "Allow OMP to deliver the queued messages on continuation, then read and "
            f"handle them. {NATIVE_QUEUED_INPUT_GUIDANCE}"
        )
    return _BacklogObservation(
        blockers=tuple(blockers),
        recovery=tuple(recovery),
        has_work=has_work,
        has_input=has_input,
    )


def _format_backlog_refusal(observed: _BacklogObservation, *, allow_yield: bool) -> str:
    parts = [
        f"{BandTool.NO_REPLY} refused: native {' '.join(observed.blockers)}.",
        " ".join(observed.recovery),
        NATIVE_REFUSED_DECLINE_GUIDANCE,
    ]
    if allow_yield:
        parts.append(NATIVE_BACKLOG_YIELD_GUIDANCE)
    parts.append(
        _backlog_reconsider_clause(
            has_work=observed.has_work, has_input=observed.has_input
        )
    )
    return " ".join(parts)


def decline_backlog_refusal(state: OmpState, *, allow_yield: bool = True) -> str | None:
    """Describe only the native backlog observed in this snapshot."""
    observed = _observe_backlog(state)
    if observed is None:
        return None
    return _format_backlog_refusal(observed, allow_yield=allow_yield)


def _sole_tool_diagnosis(
    message: OmpMessage | None, tool_call_id: str
) -> tuple[str, str] | None:
    """Return (diagnosis, recovery) when sole-tool proof fails, else None."""
    label = tool_call_id_label(tool_call_id)
    match assistant_tool_calls(message):
        case None:
            return (
                f"no sole-tool-call proof for {label} (string or missing content)",
                SOLE_TOOL_UNSTRUCTURED_RECOVERY,
            )
        case []:
            return (
                f"no sole-tool-call proof for {label} (no TOOL_CALL blocks)",
                SOLE_TOOL_UNSTRUCTURED_RECOVERY,
            )
        case [only] if only.id == tool_call_id:
            return None
        case [only]:
            diagnosis = (
                f"no sole-tool-call proof for {label} "
                f"(assistant {tool_call_id_label(only.id or '')})"
            )
            return diagnosis, SOLE_TOOL_ID_MISMATCH_RECOVERY
        case many:
            diagnosis = (
                f"no sole-tool-call proof for {label} "
                f"({len(many)} TOOL_CALL blocks in the assistant message)"
            )
            return diagnosis, SOLE_TOOL_SIBLING_RECOVERY


def _format_sole_tool_refusal(diagnosis: str, recovery: str) -> str:
    return (
        f"{BandTool.NO_REPLY} refused: {diagnosis}. {recovery} "
        f"{NATIVE_REFUSED_DECLINE_GUIDANCE}"
    )


def decline_sole_tool_refusal(
    message: OmpMessage | None, tool_call_id: str
) -> str | None:
    """Describe why the decline is not the sole tool call, or None when it is."""
    diagnosed = _sole_tool_diagnosis(message, tool_call_id)
    if diagnosed is None:
        return None
    diagnosis, recovery = diagnosed
    return _format_sole_tool_refusal(diagnosis, recovery)


def state_is_idle(state: OmpState) -> bool:
    """Positive idle proof for a snapshot: no busy signal remains set."""
    return bool(
        not state.isStreaming
        and not state.isCompacting
        and not state_has_async_backlog(state)
    )
