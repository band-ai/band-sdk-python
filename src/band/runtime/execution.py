"""
Execution - Per-room execution interface and default implementation.

Extracted from AgentSession with simplified interface.

Crash Recovery:
    When an agent restarts, it may have missed messages while down.
    The sync mechanism handles this:
    1. First WebSocket message marks the sync point (_first_ws_msg_id)
    2. Before processing WS queue, _synchronize_with_next() polls REST API
    3. Process backlog messages until we reach the sync point
    4. Clear marker and continue with WebSocket queue
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Iterator
from datetime import UTC, datetime
from enum import Enum, StrEnum
from functools import partial
from typing import (
    TYPE_CHECKING,
    Any,
    Protocol,
    runtime_checkable,
)

from band_sdk_core import (
    AgentFailure,
    ClaimRegistry,
    ParticipantRoster,
    PendingAck,
    RetryTracker,
    is_self_echo,
)

from band.client.rest import DEFAULT_REQUEST_OPTIONS
from band.client.streaming import (
    ControlMode,
    DeliveryStatus,
    MessageCreatedPayload,
    MessageMetadata,
)
from band.core.exceptions import (
    CleanupIncompleteError,
    InputNotAccepted,
    RoomExecutionStoppedError,
)
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TURN_FAILURE_PROVIDER,
    TurnResultAlreadyReported,
)
from band.core.retention import task_failure
from band.core.turn_lifecycle import (
    TurnTask,
    finished_within_grace,
    terminal_deadline,
)
from band.core.types import (
    delivery_status_for_agent,
    is_contact_hub_turn,
    metadata_to_dict,
)
from band.logging_config import TRACE_CONTEXT
from band.platform.event import (
    MessageEvent,
    ParticipantAddedEvent,
    ParticipantRemovedEvent,
    PlatformEvent,
    ReconnectedEvent,
)
from band.platform.message_lifecycle import SettlementResult
from band.runtime.context_serialization import context_item_to_dict
from band.runtime.cycle import TurnScope
from band.runtime.formatters import build_participants_message, format_history_for_llm
from band.runtime.participants import log_roster_call, log_roster_error
from band.runtime.resync import ResyncSchedule
from band.runtime.tools.agent import AgentTools
from band.runtime.types import (
    ConversationContext,
    ParticipantAddedCallback,
    ParticipantRemovedCallback,
    PlatformMessage,
    SessionConfig,
)
from band.runtime.working_state import WorkingStateReporter

if TYPE_CHECKING:
    from band.platform.link import BandLink

logger = logging.getLogger(__name__)


def _log_turn_error(error: Exception, message: str, *args: object) -> None:
    """Log a failed turn; one whose failure already reached the room was logged
    where it was reported, so it stays out of ERROR alerting."""
    reported = isinstance(error, TurnResultAlreadyReported)
    level = logging.DEBUG if reported else logging.ERROR
    logger.log(level, message, *args, exc_info=not reported)


class ResyncRequest:
    """Request reconciliation at the next serial queue boundary."""

    type: str = "_resync"  # Matches the .type attribute pattern of PlatformEvent


class BacklogProcessResult(Enum):
    ADVANCED = "advanced"
    RETRY_LATER = "retry_later"
    NOT_ACCEPTED = "not_accepted"


class ResyncOutcome(Enum):
    EMPTY = "empty"
    WORK = "work"
    BLOCKED = "blocked"
    RETRY = "retry"
    STOPPED = "stopped"


class ExecutionState(StrEnum):
    """Lifecycle state for one room execution."""

    STARTING = "starting"
    IDLE = "idle"
    PROCESSING = "processing"


def _event_message_id(event: MessageEvent) -> str:
    """The platform id of a message event that carries a real delivery."""
    if event.payload is None:
        raise ValueError("a message event without a payload has no delivery id")
    return event.payload.id


def _handler_status(owner: TurnTask | None) -> DeliveryStatus | None:
    """The terminal status a finished handler's result implies, if it left one."""
    if owner is None or not owner.task.done() or owner.task.cancelled():
        return None
    match owner.task.exception():
        case None:
            return DeliveryStatus.PROCESSED
        case InputNotAccepted() | RoomExecutionStoppedError():
            return None
        case _:
            return DeliveryStatus.FAILED


def _error_label(e: Exception) -> str:
    """Return a non-empty label for an exception, falling back to the class name."""
    return str(e).strip() or type(e).__name__


@runtime_checkable
class Execution(Protocol):
    """
    Interface for per-room execution. Pluggable.

    Implementations handle what happens INSIDE a room.
    The default ExecutionContext uses context accumulation.
    Custom implementations (e.g., Letta) can use persistent agents.

    .. versionchanged:: 0.2.0
        Breaking change: The ``stop()`` method signature changed from
        ``async def stop() -> None`` to ``async def stop(timeout=None) -> bool``.

    Migration Guide:
        If you have a custom Execution implementation, update the stop() method:

        Before::

            async def stop(self) -> None:
                # cleanup logic
                pass

        After::

            async def stop(self, timeout: float | None = None) -> bool:
                # cleanup logic (timeout can be ignored if not needed)
                return True  # Return True for graceful, False if interrupted
    """

    room_id: str

    async def start(self) -> None:
        """Start the execution context."""
        ...

    async def stop(self, timeout: float | None = None) -> bool:
        """
        Stop the execution context.

        Args:
            timeout: Optional seconds to wait for graceful shutdown.
                     None means stop immediately.

        Returns:
            True if stopped gracefully, False if cancelled mid-processing.
        """
        ...

    def inject_system_message(self, message: str) -> None:
        """
        Queue a system message for injection on next processing.

        Used by ContactEventHandler to broadcast contact changes.
        """
        ...

    async def on_event(self, event: PlatformEvent) -> None:
        """Handle a platform event for this room."""
        ...

    async def request_resync(self) -> None:
        """Signal the process loop to re-poll /next immediately.

        .. versionchanged:: 0.2.0
            Custom ``Execution`` implementations should add ``request_resync()``.
            ``AgentRuntime`` falls back safely for legacy implementations that do
            not provide it, but typed protocol conformance now includes this method.

        Called after WebSocket reconnect to catch messages that arrived while
        the socket was down. Custom implementations may provide a no-op.
        """
        ...

    def interrupt(self, *, kind: ControlMode | str = ControlMode.INTERRUPT) -> bool:
        """Abort the in-flight reasoning cycle for this room.

        Called preemptively from the WebSocket receive task on an ``interrupt``
        or ``stop`` control signal. ``AgentRuntime`` ``hasattr``-guards this, so
        custom ``Execution`` implementations that omit it degrade to a no-op.
        ``kind`` keeps ``str`` in the union because ``Protocol`` signatures
        aren't runtime-enforced and a custom implementation may still pass a
        plain string.
        """
        ...

    def stop_room(self) -> None:
        """Durably stop this room until a play signal.

        Aborts the in-flight cycle and goes quiet. Trigger suppression is
        platform-authoritative. ``AgentRuntime`` ``hasattr``-guards this.
        """
        ...

    async def resume_room(self) -> None:
        """Resume a stopped room (play): catch up rehydration-style via /next.

        ``AgentRuntime`` ``hasattr``-guards this.
        """
        ...


# Type for execution callback
ExecutionHandler = Callable[["ExecutionContext", PlatformEvent], Awaitable[None]]


class ExecutionContext:
    """
    Default execution: context accumulation model.

    Extracted from AgentSession.

    - Accumulates inputs (history, participants)
    - Queues messages
    - Feeds agent when instantiated
    - Agent disappears after execution

    Example:
        async def on_execute(ctx: ExecutionContext, event: PlatformEvent):
            if isinstance(event, MessageEvent):
                tools = AgentTools.from_context(ctx)
                history = ctx.get_history_for_llm()
                # Run LLM with context and tools...

        ctx = ExecutionContext(room_id, link, on_execute)
        await ctx.start()
    """

    def __init__(
        self,
        room_id: str,
        link: BandLink,
        on_execute: ExecutionHandler,
        config: SessionConfig | None = None,
        agent_id: str | None = None,
        on_participant_added: ParticipantAddedCallback | None = None,
        on_participant_removed: ParticipantRemovedCallback | None = None,
        *,
        hub_room_id: str | None = None,
        claim_registry: ClaimRegistry | None = None,
        on_message_control: Callable[[ExecutionContext, PlatformEvent], Awaitable[bool]]
        | None = None,
        on_platform_stop: Callable[[ExecutionContext, TurnScope], Awaitable[None]]
        | None = None,
    ):
        """
        Initialize execution context for a specific room.

        Args:
            room_id: The room this context manages
            link: BandLink for REST API calls
            on_execute: Callback for handling events
            config: Optional session configuration
            agent_id: Agent ID for filtering self-messages
            on_participant_added: Optional callback for participant_added events
            on_participant_removed: Optional callback for participant_removed events
            hub_room_id: Optional hub-room ID. Forwarded to AgentTools so the
                schema methods can auto-enable contact tools when this context
                belongs to the hub room.
            claim_registry: Optional shared message-claim registry. AgentRuntime
                passes one registry to its default contexts so a room/message
                pair executes at most once per runtime. Defaults to a private
                instance for standalone contexts.
        """
        self.room_id = room_id
        self.link = link
        self._on_execute = on_execute
        self._on_message_control = on_message_control
        self.config = config or SessionConfig()
        self._agent_id = agent_id
        self._on_participant_added = on_participant_added
        self._on_participant_removed = on_participant_removed
        self.hub_room_id = hub_room_id

        # Per-room boolean working-state reporter. Disabled for the contact-hub
        # room: it's internal housekeeping, not a peer-facing conversation, so no
        # counterpart watches a "Reasoning…" indicator there.
        self._working_reporter = WorkingStateReporter(
            self._report_working_state,
            keep_alive_seconds=self.config.working_keep_alive_seconds,
            max_working_state_seconds=self.config.max_working_state_seconds,
            enabled=(
                self.config.enable_working_state and self.room_id != self.hub_room_id
            ),
        )

        # Per-room state
        self.queue: asyncio.Queue[PlatformEvent] = asyncio.Queue()
        self.state = ExecutionState.STARTING
        self._is_running = False
        self._closing = False
        self._process_loop_task: asyncio.Task[None] | None = None
        self._context_cache: ConversationContext | None = None
        self._context_hydrated = False

        # Participant tracking. The roster is the sole source of truth for
        # membership/fields/change-detection (band_sdk_core.ParticipantRoster);
        # no shadow list is kept alongside it.
        self._roster = ParticipantRoster()
        self._participants_loaded = False

        # LLM context tracking
        self._llm_initialized = False

        # Message ownership ledger (in-flight claims, completed LRU, pending
        # acks) shared by /next and WebSocket processing. Runtime-provided so
        # all contexts of one agent coordinate; private otherwise.
        self.claims = claim_registry or ClaimRegistry()

        # Crash recovery: sync point marker and retry tracking. Attempt and
        # permanently-failed-id storage is bounded at RetryTracker's default
        # max_tracked=10_000 (oldest-first eviction) -- an intentional shared
        # memory-safety policy, not a promise to remember more than 10,000
        # distinct message ids for the life of this context.
        self._first_ws_msg_id: str | None = None  # First WS message = sync point
        self._retry_tracker = RetryTracker(max_retries=self.config.max_message_retries)
        self._sync_complete = False  # True after sync with /next completes
        self._deferred_message_id: str | None = None
        self._pending_synthetic: deque[MessageEvent] = deque()
        self._control_cursor: str | None = None

        # Graceful shutdown: event signaled when state becomes idle
        self._idle_event: asyncio.Event = asyncio.Event()
        self._idle_event.set()  # Start as idle

        # Pending system messages to inject (e.g., contact broadcasts)
        self._pending_system_messages: list[str] = []
        self._pending_resync: ResyncRequest | None = None
        self._resync_schedule = ResyncSchedule(
            self.config.idle_resync_seconds, self.config.idle_resync_max_seconds
        )
        self._resync_handled_work = False
        self._queue_wait: asyncio.Timeout | None = None

        self._detached_cycle_tasks: dict[asyncio.Task[None], TurnTask] = {}
        self.current_scope: TurnScope | None = None
        self._control_revision = 0
        self._on_platform_stop = on_platform_stop
        self._shutdown_task: asyncio.Task[bool] | None = None

        # Set when the adapter posted this turn's failure itself (via
        # ``AgentTools.send_failure``), so the runtime notice doesn't repeat it.
        # Cleared in the per-message ``finally``.
        self._turn_failure_reported: bool = False

        # Received STOP suppresses local triggers. Base-interval probes discover
        # a missed PLAY; platform claims remain authoritative.
        self._stopped: bool = False
        # The first stop's graceful result; a retry only settles retained work.
        self._stop_graceful = True

        # Optional seam for clearing user-visible activity state ("reasoning…")
        # when a cycle is interrupted/stopped. Filled by the activity-signal
        # work; a no-op (None) here.
        self._on_activity_clear: Callable[[], Awaitable[None]] | None = None

    @property
    def thread_id(self) -> str:
        """LangGraph thread_id = room_id."""
        return self.room_id

    @property
    def is_processing(self) -> bool:
        """Check if context is currently processing an event."""
        return self.state is ExecutionState.PROCESSING

    @property
    def is_stopped(self) -> bool:
        """Whether a received STOP control has paused this room."""
        return self._stopped

    @property
    def adapter_stop_notified(self) -> bool:
        """Whether an observed STOP already notified the adapter for this turn."""
        scope = self.current_scope
        return scope is not None and scope.observer_task is not None

    def _set_state(self, new_state: ExecutionState) -> None:
        """
        Set the execution state and update the idle event accordingly.

        This ensures the idle event is properly synchronized with state changes
        for graceful shutdown coordination.
        """
        self.state = new_state
        if new_state is ExecutionState.PROCESSING:
            self._idle_event.clear()
        else:
            self._idle_event.set()

    @property
    def is_running(self) -> bool:
        """Check if context is running (task exists and not done)."""
        return (
            self._process_loop_task is not None and not self._process_loop_task.done()
        )

    @property
    def participants(self) -> list[dict[str, Any]]:
        """Get current participants list (a fresh snapshot from the roster)."""
        return self._roster.list()

    @property
    def agent_id(self) -> str | None:
        """This agent's own ID, used to exclude itself from mention lists."""
        return self._agent_id

    @property
    def is_llm_initialized(self) -> bool:
        """Check if LLM has been initialized with system prompt."""
        return self._llm_initialized

    def mark_llm_initialized(self) -> None:
        """Mark that system prompt has been sent to LLM."""
        self._llm_initialized = True
        logger.debug("ExecutionContext %s: LLM initialized", self.room_id)

    def _delivery_status_for_agent(self, metadata: Any) -> str | None:
        """Return this agent's delivery status from message metadata."""
        return delivery_status_for_agent(metadata, self._agent_id)

    def _context_message_metadata(self, message_id: str) -> dict[str, Any] | None:
        """Return metadata for a hydrated context message by ID."""
        if not self._context_cache:
            return None

        for message in self._context_cache.messages:
            if message.get("id") == message_id:
                return metadata_to_dict(message.get("metadata"))

        return None

    def _message_processed_for_agent(self, message_id: str, metadata: Any) -> bool:
        """Check whether platform metadata says this agent processed a message."""
        if self._delivery_status_for_agent(metadata) == DeliveryStatus.PROCESSED:
            return True

        context_metadata = self._context_message_metadata(message_id)
        return (
            self._delivery_status_for_agent(context_metadata)
            == DeliveryStatus.PROCESSED
        )

    async def _retry_terminal_ack(self, message_id: str) -> bool:
        """Retry one pending terminal write; True once nothing remains to retry now.

        A parked entry stays retained but is not retried or blocking: only a
        reconnect or rejoin unparks it. An unconfirmed write keeps its stored
        intent however long it takes -- dropping it would replay the handler.
        """
        pending = self.claims.pending_ack(self.room_id, message_id)
        if pending is None or pending["parked"] is not None:
            return True
        if self._stopped:
            return False

        try:
            result = await self._write_pending(message_id)
        except RoomExecutionStoppedError:
            return False
        if result is not SettlementResult.UNCONFIRMED:
            return True

        if self._stopped:
            return False
        retries = self.claims.record_ack_retry(self.room_id, message_id)
        if retries == self._retry_tracker.max_retries:
            logger.warning(
                "ExecutionContext %s: terminal write still unconfirmed for message %s; retaining its intent",
                self.room_id,
                message_id,
            )
        return False

    async def _write_pending(self, message_id: str) -> SettlementResult:
        """Send the one write a pending entry owes and record what it established."""
        pending = self.claims.pending_ack(self.room_id, message_id)
        if pending is None:
            raise ValueError(f"message {message_id} has no pending terminal write")
        result = await self._write_terminal(message_id, pending)
        match result:
            case SettlementResult.ACCEPTED:
                self._retry_tracker.mark_success(message_id)
                self.claims.remember_completed(self.room_id, message_id)
            case SettlementResult.NOT_FOUND | SettlementResult.REJECTED:
                self.claims.park_ack(self.room_id, message_id, result)
                logger.warning(
                    "ExecutionContext %s: terminal write for message %s parked (%s); "
                    "it is not retried or replayed until the room is rejoined",
                    self.room_id,
                    message_id,
                    result,
                )
            case SettlementResult.UNCONFIRMED:
                pass
        return result

    async def _write_terminal(
        self, message_id: str, pending: PendingAck
    ) -> SettlementResult:
        match pending["status"]:
            case DeliveryStatus.PROCESSED:
                return await self.link.mark_processed_result(self.room_id, message_id)
            case DeliveryStatus.FAILED:
                return await self.link.mark_failed_result(
                    self.room_id, message_id, pending["error"]
                )
        raise ValueError(f"unknown pending terminal intent {pending['status']!r}")

    async def _settle_originals(self, scope: TurnScope) -> bool:
        """Write every original's stored intent; False while any remains retryable.

        Each original is attempted even if an earlier one fails, so one bad
        write cannot starve its siblings.
        """
        results = [
            await self._write_pending(_event_message_id(event))
            for event in scope.originals
        ]
        return SettlementResult.UNCONFIRMED not in results

    @contextlib.contextmanager
    def _claim_message(self, message_id: str) -> Iterator[bool]:
        """Yield whether an in-flight claim was acquired; release iff acquired.

        ``band_sdk_core`` has no context-manager equivalent by design, so
        this is the sole SDK adapter over ``try_claim``/``.release``.
        """
        acquired = self.claims.try_claim(self.room_id, message_id)
        try:
            yield acquired
        finally:
            if acquired:
                self.claims.release(self.room_id, message_id)

    @contextlib.contextmanager
    def _claim_messages(self, message_ids: tuple[str, ...]) -> Iterator[bool]:
        """Yield whether every id was claimed; a conflict releases this call's claims."""
        with contextlib.ExitStack() as stack:
            yield all(
                stack.enter_context(self._claim_message(message_id))
                for message_id in message_ids
            )

    # --- Execution protocol implementation ---

    async def start(self) -> None:
        """
        Start background processing for this room.

        Creates an asyncio task that processes events from the queue.
        """
        if self._is_running:
            logger.warning("ExecutionContext %s already running", self.room_id)
            return

        logger.info("Starting ExecutionContext for room: %s", self.room_id)
        self._closing = False
        self._is_running = True
        self._unpark_terminal_acks()
        self._process_loop_task = asyncio.create_task(
            self._process_loop(),
            name=f"execution-{self.room_id}",
        )

    async def stop(self, timeout: float | None = None) -> bool:
        """
        Stop processing with optional graceful timeout.

        If timeout is provided, waits up to that many seconds for current
        message processing to complete before cancelling. If timeout is None,
        cancels immediately via task.cancel().

        Args:
            timeout: Optional seconds to wait for current processing to complete.
                     None means cancel immediately.

        Returns:
            True if stopped gracefully (processing completed or was idle),
            False if had to cancel mid-processing after timeout.

        Raises:
            CleanupIncompleteError: Cancelled work outlived the settlement
                budget. It stays retained, and a later ``stop()`` settles it.
        """
        self._closing = True
        if self._shutdown_task is None or self._shutdown_task.done():
            self._shutdown_task = asyncio.create_task(self._stop(timeout))
            self._shutdown_task.add_done_callback(task_failure)
        return await asyncio.shield(self._shutdown_task)

    async def _stop(self, timeout: float | None) -> bool:
        if self._process_loop_task is not None:
            self._stop_graceful = True
            if timeout is not None and self.is_processing:
                self._stop_graceful = await self._wait_for_idle(timeout)
            # A released worker belongs to adapter room cleanup, not shutdown.
            if (scope := self._running_scope) is not None:
                scope.terminate(shutdown=True)
            self._is_running = False
            loop_owner = TurnTask(self._process_loop_task)
            loop_owner.terminate()
            await self._drain_or_retain(loop_owner)
            self._process_loop_task = None
        await self._stop_retained_tasks(terminal_deadline())
        await self._working_reporter.stop()
        return self._stop_graceful

    async def _wait_for_idle(self, timeout: float) -> bool:
        """
        Wait for the execution to become idle (not processing).

        Uses event-based waiting for efficient notification when processing completes.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            True if became idle within timeout, False if timed out.
        """
        if not self.is_processing:
            return True

        try:
            await asyncio.wait_for(self._idle_event.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    async def on_event(self, event: PlatformEvent) -> None:
        """
        Handle a platform event.

        Called by RoomPresence/AgentRuntime when an event arrives.
        Tracks first WebSocket message ID for crash recovery sync.
        Reconnect events trigger a fresh /next synchronization immediately.
        """
        if isinstance(event, ReconnectedEvent):
            logger.info(
                "ExecutionContext %s: Reconnected, scheduling synchronization",
                self.room_id,
            )
            self._unpark_terminal_acks()
            await self.request_resync()
            return

        # Track first WebSocket message ID for sync point
        if isinstance(event, MessageEvent) and self._first_ws_msg_id is None:
            msg_id = event.payload.id if event.payload else None
            if msg_id:
                self._first_ws_msg_id = msg_id
                logger.debug("Sync point marker set: %s", msg_id)

        self.queue.put_nowait(event)
        logger.debug("Event %s enqueued for room %s", event.type, self.room_id)

    async def request_resync(self) -> None:
        """Queue an immediate /next reconciliation, coalescing pending requests."""
        if self._pending_resync is not None:
            return
        self._pending_resync = ResyncRequest()
        self.queue.put_nowait(self._pending_resync)  # type: ignore[arg-type]  # The sentinel only wakes this loop.
        logger.debug("ExecutionContext %s: Resync sentinel enqueued", self.room_id)

    def interrupt(self, *, kind: ControlMode | str = ControlMode.INTERRUPT) -> bool:
        """Abort the in-flight reasoning cycle, if any.

        Called from the WebSocket receive task. It only terminates the running
        turn scope; all message-status / dedupe bookkeeping happens once in
        ``_run_cycle``, never here, so the two coroutines do not race on shared
        state.

        Args:
            kind: ``ControlMode.INTERRUPT`` (consume the message) or
                ``ControlMode.STOP`` (leave it actionable for replay on play).
                Distinguishes the two unwind paths in ``_run_cycle``. A plain
                matching string coerces; anything else raises ``ValueError``.

        Returns:
            True if the signal terminated an admitted, unsettled turn scope,
            whether or not its handler has started. Between cycles this is a
            clean no-op and cannot affect the next cycle.

        Raises:
            ValueError: ``kind`` is not a valid ``ControlMode``, or is
                ``ControlMode.PLAY`` — this method only ever implements
                interrupt/stop; use ``resume_room()`` for play.
        """
        kind = ControlMode(kind)
        if kind is ControlMode.PLAY:
            raise ValueError("interrupt() does not accept kind=PLAY; use resume_room()")

        self._control_revision += 1
        return self._cancel_cycle(kind)

    @property
    def _running_scope(self) -> TurnScope | None:
        """The current scope between admission and settlement."""
        scope = self.current_scope
        if scope is None or scope.settled or not scope.admitted:
            return None
        return scope

    @property
    def _active_cycle_task(self) -> asyncio.Task[None] | None:
        scope = self._running_scope
        if scope is None or scope.handler is None:
            return None
        return scope.handler.task

    def _cancel_cycle(self, kind: ControlMode) -> bool:
        scope = self._running_scope
        if scope is None:
            return False
        scope.terminate(kind)
        return True

    def stop_room(self) -> None:
        """Pause triggers and abort the cycle, leaving it actionable for PLAY."""
        self._stopped = True
        self._reset_resync_interval()
        self.interrupt(kind=ControlMode.STOP)

    async def resume_room(self) -> None:
        """Clear the local pause and request immediate reconciliation."""
        self._resume_room()
        await self.request_resync()

    def _resume_room(self) -> None:
        self._control_revision += 1
        self._stopped = False
        self._reset_resync_interval()

    def _reset_resync_interval(self) -> None:
        if self._resync_schedule.due_at is None:
            return
        self._resync_schedule.reset(asyncio.get_running_loop().time())
        if self._queue_wait is not None and not self._queue_wait.expired():
            self._queue_wait.reschedule(self._resync_schedule.due_at)

    def owns_stop_observation(self, scope: TurnScope) -> bool:
        return self._owns_control_revision(scope, scope.stop_revision)

    def _owns_control_revision(self, scope: TurnScope, revision: int | None) -> bool:
        return self.current_scope is scope and revision == self._control_revision

    def observe_platform_stop(
        self,
        scope: TurnScope,
        *,
        control_revision: int | None = None,
        cooperative: bool = False,
    ) -> None:
        if not scope.stop_observed:
            scope.stop_revision = (
                scope.control_revision if control_revision is None else control_revision
            )
        scope.terminate(ControlMode.STOP, cooperative=cooperative, observed=True)
        if self.current_scope is not scope:
            return
        if (
            not self._closing
            and self.owns_stop_observation(scope)
            and scope.observer_task is None
            and self._on_platform_stop is not None
        ):
            scope.observer_task = asyncio.create_task(self._notify_platform_stop(scope))
            self._retain_detached_cycle_task(scope.observer_task)

    async def _notify_platform_stop(self, scope: TurnScope) -> None:
        if self.owns_stop_observation(scope) and self._on_platform_stop is not None:
            await self._on_platform_stop(self, scope)

    async def _begin_scope(self) -> TurnScope | None:
        previous = self.current_scope
        if (
            previous is not None
            and previous.observer_task is not None
            and not await finished_within_grace(previous.observer_task)
        ):
            logger.warning(
                "ExecutionContext %s: platform-stop observer is still running; "
                "deferring the next turn",
                self.room_id,
            )
            return None
        self.current_scope = TurnScope(self._control_revision)
        return self.current_scope

    async def _stop_retained_tasks(self, deadline: float) -> None:
        owners = tuple(self._detached_cycle_tasks.values())
        for owner in owners:
            owner.retry(deadline=deadline)
        for owner in owners:
            await owner.drain()
        if unfinished := sum(not task.done() for task in self._detached_cycle_tasks):
            raise CleanupIncompleteError(
                f"ExecutionContext {self.room_id}: {unfinished} cancelled "
                "task(s) still running"
            )

    async def _clear_activity(self) -> None:
        """Invoke the optional activity-clear seam after an aborted cycle."""
        if self._on_activity_clear is None:
            return
        try:
            await self._on_activity_clear()
        except Exception:
            logger.exception(
                "ExecutionContext %s: activity-clear hook failed", self.room_id
            )

    # --- Participant management ---

    def add_participant(self, participant: dict[str, Any]) -> bool:
        """
        Add or refresh a participant (from a WebSocket event or a tool's
        REST resync).

        An existing id is merged field-by-field rather than skipped or
        replaced: a field learned after the participant was first tracked
        (e.g. a description that arrives via a later REST fetch) reaches the
        roster, while a sparser source (e.g. a WS payload without description)
        cannot erase what an earlier source already knew.

        Returns:
            True if newly added, False if it already existed (and was refreshed)

        Raises:
            TypeError: ``participant`` is not a mapping, its ``id`` is
                missing/not a string, or another tracked field is not a
                string or ``None`` (``band_sdk_core.ParticipantRoster.add``).
        """
        added = self._roster.add(participant)
        if added:
            logger.debug(
                "ExecutionContext %s: Added participant %s",
                self.room_id,
                participant.get("name"),
            )
        return added

    def remove_participant(self, participant_id: str) -> bool:
        """
        Remove participant (from WebSocket event).

        Returns:
            True if removed, False if not found
        """
        return self._roster.remove(participant_id)

    def set_participants(self, participants: list[dict[str, Any]]) -> None:
        """Replace the roster from an authoritative snapshot (a REST list).

        Membership follows the snapshot exactly — stale entries drop out —
        while fields merge per id, so a source that omits a field (e.g. the
        participants list endpoint carries no description) cannot erase one
        learned elsewhere.

        Raises:
            ValueError: ``participants`` names an id more than once
                (``.issues``/``.trace_context`` attached); the roster is left
                unchanged.
        """
        # TRACE_CONTEXT is set for the duration of the current turn (see
        # logging_config.trace_context_scope); read fresh here rather than
        # threaded in as a parameter so this always reflects whichever turn
        # is actually calling set_participants right now. None outside a
        # turn (e.g. bootstrap before any event has been processed).
        self._roster.set_all(participants, trace_context=TRACE_CONTEXT.get())

    def participants_changed(self) -> bool:
        """Check if membership or any tracked field changed since the last
        mark_participants_sent() — an id-only diff would miss a participant
        refreshed in place (e.g. a description learned after it first joined)."""
        return self._roster.changed()

    def mark_participants_sent(
        self, snapshot: list[dict[str, Any]] | None = None
    ) -> None:
        """Commit a transmitted roster only while that snapshot is current."""
        if snapshot is None or self.participants == snapshot:
            self._roster.mark_sent()

    def inject_system_message(self, message: str) -> None:
        """
        Queue a system message for injection on next processing.

        Used by ContactEventHandler to broadcast contact changes
        into all active sessions.

        Args:
            message: System message to inject
        """
        self._pending_system_messages.append(message)
        logger.debug(
            "ExecutionContext %s: Queued system message: %s",
            self.room_id,
            message[:50],
        )

    def peek_pending_system_messages(self) -> list[str]:
        """Prepare pending context without consuming it."""
        return self._pending_system_messages.copy()

    def commit_system_messages(self, prepared: list[str]) -> None:
        """Consume only the prepared prefix, preserving concurrent arrivals."""
        if self._pending_system_messages[: len(prepared)] == prepared:
            del self._pending_system_messages[: len(prepared)]

    def get_pending_system_messages(self) -> list[str]:
        """
        Get and clear pending system messages.

        Returns:
            List of pending messages (cleared after call)
        """
        messages = self._pending_system_messages.copy()
        self._pending_system_messages.clear()
        return messages

    async def load_participants(self) -> list[dict[str, Any]]:
        """Load participants from API."""
        if self._participants_loaded:
            return self._roster.list()

        try:
            response = await self.link.rest.agent_api_participants.list_agent_chat_participants(
                chat_id=self.room_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            # `is not None`, not truthiness: an authoritative empty snapshot
            # (`response.data == []`) must still clear the roster instead of
            # being skipped as if nothing came back.
            if response.data is not None:
                self.set_participants([p.model_dump() for p in response.data])
            self._participants_loaded = True
        except Exception as e:  # noqa: BLE001 -- runtime loop must log and continue rather than crash the agent process
            # Catches both the REST call (any exception) and set_participants
            # (ValueError on a duplicate id) -- band_sdk_core failures carry
            # .issues/.trace_context, which a bare "%s" would only stringify.
            log_roster_error(
                logger, room_id=self.room_id, action="load participants", err=e
            )
            self._participants_loaded = True

        return self._roster.list()

    # --- Context building ---

    async def hydrate(self) -> None:
        """
        Hydrate conversation context for this room.

        Called lazily on first event to load participant list and
        (optionally) conversation history.

        Participants are always loaded (lightweight, universally needed).
        If enable_context_hydration is False, skips history loading
        (useful for agents that manage their own state like Letta).
        """
        if self._context_hydrated:
            return

        # Always load participants (lightweight, universally needed)
        participants = await self.load_participants()

        # Skip history hydration if disabled
        if not self.config.enable_context_hydration:
            logger.debug("History hydration disabled for room: %s", self.room_id)
            # Reuses load_participants()'s own snapshot -- nothing awaited since
            # that call returned, so the roster cannot have changed underneath it.
            self._context_cache = ConversationContext(
                room_id=self.room_id,
                messages=[],
                participants=participants,
                hydrated_at=datetime.now(UTC),
            )
            self._context_hydrated = True
            return

        logger.debug("Hydrating context for room: %s", self.room_id)

        try:
            # Load context from API
            context_response = (
                await self.link.rest.agent_api_context.get_agent_chat_context(
                    chat_id=self.room_id,
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
            )

            messages = []
            if context_response.data:
                for item in context_response.data:
                    messages.append(context_item_to_dict(item))

            self._context_cache = ConversationContext(
                room_id=self.room_id,
                messages=messages,
                participants=self._roster.list(),
                hydrated_at=datetime.now(UTC),
            )
            self._context_hydrated = True

            logger.debug(
                "Context hydrated: %s messages, %s participants",
                len(messages),
                len(self._context_cache.participants),
            )

        except Exception as e:  # noqa: BLE001 -- runtime loop must log and continue rather than crash the agent process
            logger.warning("Context hydration failed: %s", e)
            self._context_cache = ConversationContext(
                room_id=self.room_id,
                messages=[],
                participants=self._roster.list(),
                hydrated_at=datetime.now(UTC),
            )
            self._context_hydrated = True

    def _is_context_cache_expired(self) -> bool:
        """Check whether the hydrated context cache has exceeded its TTL."""
        if self._context_cache is None:
            return False

        ttl_seconds = self.config.context_cache_ttl_seconds
        if ttl_seconds <= 0:
            return True

        age_seconds = (
            datetime.now(UTC) - self._context_cache.hydrated_at
        ).total_seconds()
        return age_seconds > ttl_seconds

    def _invalidate_context_cache(self) -> None:
        """Clear hydrated context so the next access refreshes it."""
        self._context_cache = None
        self._context_hydrated = False

    def _expire_context_cache_if_needed(self) -> bool:
        """Invalidate stale cached context before it can be returned."""
        if not self._is_context_cache_expired():
            return False

        logger.debug("ExecutionContext %s: Context cache expired", self.room_id)
        self._invalidate_context_cache()
        return True

    async def _ensure_fresh_context(self, *, force_refresh: bool = False) -> None:
        """Hydrate context if missing, expired, or explicitly refreshed."""
        if force_refresh:
            self._invalidate_context_cache()
        else:
            self._expire_context_cache_if_needed()

        if not self._context_hydrated:
            await self.hydrate()

    def build_context(self) -> ConversationContext:
        """
        Build context dict for LLM.

        Returns cached context or empty context if not hydrated.
        """
        self._expire_context_cache_if_needed()
        if self._context_cache:
            # Participants are not part of the TTL-governed cache: the roster
            # is live SDK state (add_participant/remove_participant mutate it
            # mid-cycle), so every call refreshes this field from the roster
            # instead of returning whatever snapshot was baked in at hydrate
            # time. messages/hydrated_at are unaffected -- no second REST call.
            self._context_cache.participants = self._roster.list()
            return self._context_cache

        return ConversationContext(
            room_id=self.room_id,
            messages=[],
            participants=self._roster.list(),
            hydrated_at=datetime.now(UTC),
        )

    async def get_context(self, force_refresh: bool = False) -> ConversationContext:
        """
        Get conversation context (lazy, cached).

        Args:
            force_refresh: Force refresh from API even if cached
        """
        await self._ensure_fresh_context(force_refresh=force_refresh)

        return self.build_context()

    def get_history_for_llm(
        self, exclude_message_id: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Get conversation history formatted for LLM injection.

        Returns list of dicts with:
        - role: "assistant" or "user"
        - content: message content
        - sender_name: original sender name

        Args:
            exclude_message_id: Message ID to exclude (usually current message)

        Returns:
            List of message dicts ready for LLM formatting.
        """
        self._expire_context_cache_if_needed()
        if not self.config.enable_context_hydration:
            return []

        if not self._context_cache:
            return []

        return format_history_for_llm(
            self._context_cache.messages,
            exclude_id=exclude_message_id,
            participants=self._roster.list(),
        )

    def build_participants_message(self) -> str:
        """Build a system message with current participant list for LLM."""
        return build_participants_message(self._roster.list())

    async def _notify_participant_added(self, event: ParticipantAddedEvent) -> None:
        """Fire optional participant-added callback without breaking execution."""
        if self._on_participant_added is None:
            return

        try:
            await self._on_participant_added(self.room_id, event)
        except Exception:
            logger.exception("on_participant_added error for %s", self.room_id)

    async def _notify_participant_removed(self, event: ParticipantRemovedEvent) -> None:
        """Fire optional participant-removed callback without breaking execution."""
        if self._on_participant_removed is None:
            return

        try:
            await self._on_participant_removed(self.room_id, event)
        except Exception:
            logger.exception("on_participant_removed error for %s", self.room_id)

    # --- Internal processing ---

    async def _process_loop(self) -> None:
        """Synchronize startup, then serialize queued events and due recovery."""
        try:
            # A blocked startup yields to the scheduler so controls can reach
            # active work without letting newer ordinary messages run.
            await self._synchronize_with_next()

            self._set_state(ExecutionState.IDLE)
            logger.info(
                "ExecutionContext %s: Startup pass finished; servicing queue and recovery",
                self.room_id,
            )

            loop = asyncio.get_running_loop()
            self._resync_schedule.start(loop.time())
            while True:
                deadline = self._resync_schedule.due_at
                if deadline is not None and deadline <= loop.time():
                    await self._reconcile()
                    continue
                try:
                    # Keep cancellation on the owner task so shutdown cannot
                    # be lost in a child queue-wait task on Windows.
                    async with asyncio.timeout_at(deadline) as self._queue_wait:
                        event = await self.queue.get()
                except TimeoutError:
                    continue
                finally:
                    self._queue_wait = None

                if isinstance(event, ResyncRequest):
                    if event is self._pending_resync:
                        await self._reconcile()
                    continue
                if isinstance(event, MessageEvent) and (
                    not self._sync_complete or self._has_pending_work
                ):
                    await self._retain_or_handle_control(event)
                    continue
                if (
                    await self._process_event(event) is False
                    and not self._has_pending_work
                ):
                    await self._reconcile()

        except asyncio.CancelledError:
            logger.debug("ExecutionContext %s cancelled", self.room_id)
        except Exception:
            logger.exception("ExecutionContext %s error", self.room_id)
        logger.debug("ExecutionContext %s loop exited", self.room_id)

    @property
    def _has_pending_work(self) -> bool:
        return self._deferred_message_id is not None or bool(self._pending_synthetic)

    def _set_deferred_head(self, message_id: str | None) -> None:
        if message_id != self._deferred_message_id:
            self._deferred_message_id = message_id
            self._control_cursor = None

    def _defer_unaccepted_input(self, message_id: str | None) -> None:
        if message_id:
            self._set_deferred_head(message_id)
        logger.debug(
            "ExecutionContext %s: Input %s not accepted; "
            "keeping it pending for scheduler recovery",
            self.room_id,
            message_id or "synthetic",
        )

    def _retain_synthetic(
        self, event: PlatformEvent | None, *, first: bool = False
    ) -> bool:
        if not isinstance(event, MessageEvent) or event.payload is None:
            return False
        payload = event.payload
        if not is_contact_hub_turn(
            sender_type=payload.sender_type, sender_id=payload.sender_id
        ):
            return False
        if not any(
            item.payload is not None and item.payload.id == payload.id
            for item in self._pending_synthetic
        ):
            if first:
                self._pending_synthetic.appendleft(event)
            else:
                self._pending_synthetic.append(event)
        return True

    async def _retain_or_handle_control(self, event: MessageEvent) -> None:
        if self._retain_synthetic(event):
            return
        if not self._stopped and self._on_message_control is not None:
            await self._scan_controls()

    async def _scan_controls(self) -> None:
        if self._on_message_control is None:
            return
        revision = self._control_revision
        try:
            page = await self.link.get_actionable_page(
                self.room_id, cursor=self._control_cursor
            )
            if revision != self._control_revision:
                return
            for msg in page.messages:
                if self._stopped or revision != self._control_revision:
                    return
                if msg.id != self._deferred_message_id:
                    await self._try_control(_backlog_event(msg))
            self._control_cursor = page.next_cursor
        except RoomExecutionStoppedError:
            return
        except Exception:
            logger.exception("Control recovery failed for room %s", self.room_id)

    async def _try_control(self, event: MessageEvent) -> bool:
        """Probe a gated candidate without invoking or charging model work."""
        if self._on_message_control is None or event.payload is None:
            return False
        payload = event.payload
        msg_id = payload.id
        if self._agent_id and is_self_echo(
            sender_id=payload.sender_id or "",
            sender_type=payload.sender_type or "",
            agent_id=self._agent_id,
        ):
            return False
        if self.claims.is_completed(
            self.room_id, msg_id
        ) or self._retry_tracker.is_permanently_failed(msg_id):
            return False
        if self.claims.is_ack_pending(self.room_id, msg_id):
            await self._retry_terminal_ack(msg_id)
            return True
        if self._message_processed_for_agent(msg_id, payload.metadata):
            self.claims.remember_completed(self.room_id, msg_id)
            return False
        with self._claim_message(msg_id) as acquired:
            if not acquired or (scope := await self._begin_scope()) is None:
                return False
            revision = self._control_revision
            scope.originals = (event,)

            async def probe() -> bool:
                return await self._on_message_control(self, event)

            try:
                if not await self.link.mark_processing(self.room_id, msg_id):
                    return False
                if not self._owns_attempt(scope, revision):
                    return False
                scope.admitted = True
                await self._ensure_fresh_context()
                if revision != self._control_revision:
                    return False
                if not await self._run_cycle(scope, event, handler=probe):
                    return False
                if not scope.consumed:
                    return False
                await self._settle_originals(scope)
                return True
            except RoomExecutionStoppedError:
                return False
            except Exception as error:  # noqa: BLE001 -- control boundary uses the normal delivery-failure policy
                _log_turn_error(error, "Error handling control message %s", msg_id)
                attempts, _ = self._retry_tracker.record_attempt(msg_id)
                await self._handle_turn_failure(msg_id, attempts, error)
                if attempts >= self._retry_tracker.max_retries:
                    self._retry_tracker.mark_permanently_failed(msg_id)
                return False
            finally:
                scope.settled = True
                self._turn_failure_reported = False
                self._set_state(ExecutionState.IDLE)

    async def _retry_pending_terminal_acks(self) -> bool:
        """Retry every unparked pending write once; False while any stays retryable."""
        resolved = [
            await self._retry_terminal_ack(message_id)
            for message_id in self.claims.pending_ack_ids(self.room_id)
        ]
        return all(resolved)

    def _unpark_terminal_acks(self) -> None:
        """Let a reconnect or rejoin retry every refused terminal write once."""
        if unparked := self.claims.unpark_acks(self.room_id):
            logger.info(
                "ExecutionContext %s: retrying %s parked terminal write(s)",
                self.room_id,
                unparked,
            )

    async def _reconcile(self) -> None:
        # Requests received after this pass starts retain their own sentinel.
        self._pending_resync = None
        if (
            not self._sync_complete
            and not self._stopped
            and not await self._synchronize_with_next()
        ):
            outcome = ResyncOutcome.BLOCKED
        else:
            outcome = await self._wait_until_resync_complete()
        if self._has_pending_work and not self._stopped:
            await self._scan_controls()
        self._resync_schedule.complete(
            asyncio.get_running_loop().time(),
            empty=outcome is ResyncOutcome.EMPTY and not self._stopped,
        )

    async def _wait_until_resync_complete(self) -> ResyncOutcome:
        """Preserve recovery ordering while allowing stopped-room discovery."""
        candidate: PlatformMessage | None = None
        revision = self._control_revision
        if self._stopped:
            try:
                candidate = await self._get_next_message()
            except Exception:
                logger.exception("Stopped-room probe failed for %s", self.room_id)
                return ResyncOutcome.RETRY
            if revision != self._control_revision:
                return ResyncOutcome.BLOCKED
            if candidate is None:
                return ResyncOutcome.STOPPED
            if self._backlog_is_nonrunnable(candidate):
                return ResyncOutcome.STOPPED
            self._resume_room()
            revision = self._control_revision

        if await self._retry_pending_terminal_acks():
            if revision != self._control_revision:
                return ResyncOutcome.BLOCKED
            outcome = await self._resync_pending_messages(candidate)
            if outcome is not ResyncOutcome.RETRY:
                return outcome
        if self._stopped:
            return ResyncOutcome.STOPPED
        self._set_state(ExecutionState.IDLE)
        return ResyncOutcome.RETRY

    async def _synchronize_with_next(self) -> bool:
        """
        Synchronize backlog via /next API until caught up with WebSocket.

        /next includes unfinished processing messages and gates stopped rooms.
        Process in platform order until empty or the first queued WebSocket
        message is reached; remove its queued duplicate before continuing.
        """
        logger.debug(
            "ExecutionContext %s: Starting /next synchronization", self.room_id
        )

        previous_id: str | None = None
        try:
            while True:
                revision = self._control_revision
                next_msg = await self._get_next_message()
                if revision != self._control_revision:
                    return self._stopped
                if (
                    next_msg is not None
                    and next_msg.id == previous_id
                    and self._backlog_is_nonrunnable(next_msg)
                ):
                    break
                previous_id = next_msg.id if next_msg is not None else None

                if next_msg is None:
                    logger.debug(
                        "ExecutionContext %s: /next returned None, synced",
                        self.room_id,
                    )
                    self._sync_complete = True
                    return True

                if self._retry_tracker.is_permanently_failed(next_msg.id):
                    logger.warning(
                        "ExecutionContext %s: Skipping permanently failed message %s",
                        self.room_id,
                        next_msg.id,
                    )
                    break

                at_sync_point = next_msg.id == self._first_ws_msg_id
                level = logging.INFO if at_sync_point else logging.DEBUG
                message = (
                    "ExecutionContext %s: Sync point reached at message %s"
                    if at_sync_point
                    else "ExecutionContext %s: Processing backlog message %s"
                )
                logger.log(level, message, self.room_id, next_msg.id)
                result = await self._process_backlog_message(
                    next_msg, revision=revision
                )
                if result in {
                    BacklogProcessResult.RETRY_LATER,
                    BacklogProcessResult.NOT_ACCEPTED,
                }:
                    self._set_deferred_head(next_msg.id)
                    return False
                if at_sync_point:
                    # Preserve other queued events when removing the sync point.
                    self._drain_duplicate_from_queue(next_msg.id)
                    self._first_ws_msg_id = None
                    self._sync_complete = True
                    return True

                if self._stopped:
                    # A stop control signal landed mid-cycle: the message was
                    # deliberately left in 'processing' for replay on play, but
                    # /next excludes only 'processed' messages, so the next
                    # iteration would just re-fetch and fully re-run the very
                    # cycle stop just aborted. Pause here instead; play's
                    # resync will pick the message back up.
                    logger.debug(
                        "ExecutionContext %s: stopped mid-backlog-sync, "
                        "pausing /next polling",
                        self.room_id,
                    )
                    break

                if self._retry_tracker.is_permanently_failed(next_msg.id):
                    logger.warning(
                        "ExecutionContext %s: Message %s permanently failed",
                        self.room_id,
                        next_msg.id,
                    )
                    break

        except Exception:
            logger.exception("ExecutionContext %s: Sync error", self.room_id)
            return False

        logger.debug("ExecutionContext %s: Synchronization complete", self.room_id)
        self._sync_complete = True
        return True

    async def _get_next_message(self) -> PlatformMessage | None:
        """
        Get next unprocessed message from REST API.

        Returns None if no more messages in backlog (204 No Content).
        Delegates to BandLink.get_next_message().
        """
        return await self.link.get_next_message(self.room_id)

    async def _resync_pending_messages(
        self, candidate: PlatformMessage | None = None
    ) -> ResyncOutcome:
        """Drain actionable work, distinguishing empty from blocked recovery."""
        self._resync_handled_work = False
        if self._deferred_message_id is None:
            while self._pending_synthetic:
                event = self._pending_synthetic.popleft()
                if not await self._process_event(event):
                    return ResyncOutcome.BLOCKED
        previous_id: str | None = None
        try:
            while True:
                revision = self._control_revision
                next_msg = candidate
                candidate = None
                if next_msg is None:
                    next_msg = await self._get_next_message()
                if revision != self._control_revision:
                    return ResyncOutcome.BLOCKED
                if next_msg is None:
                    self._set_deferred_head(None)
                    if self._pending_synthetic:
                        return ResyncOutcome.BLOCKED
                    return (
                        ResyncOutcome.WORK
                        if self._resync_handled_work
                        else ResyncOutcome.EMPTY
                    )
                if (
                    next_msg.id == previous_id
                    and self._backlog_is_nonrunnable(next_msg)
                ) or self._retry_tracker.is_permanently_failed(next_msg.id):
                    return ResyncOutcome.BLOCKED
                previous_id = next_msg.id
                result = await self._process_backlog_message(
                    next_msg, revision=revision
                )
                if self._stopped:
                    return ResyncOutcome.STOPPED
                if revision != self._control_revision:
                    return ResyncOutcome.BLOCKED
                if result is BacklogProcessResult.NOT_ACCEPTED:
                    return ResyncOutcome.BLOCKED
                if result is BacklogProcessResult.RETRY_LATER:
                    self._set_deferred_head(next_msg.id)
                    return ResyncOutcome.RETRY
                if next_msg.id == self._deferred_message_id:
                    self._set_deferred_head(None)
                if self._pending_synthetic:
                    return ResyncOutcome.BLOCKED
        except Exception:
            logger.exception(
                "ExecutionContext %s: Error during /next resync", self.room_id
            )
            return ResyncOutcome.RETRY

    def _backlog_is_nonrunnable(self, msg: PlatformMessage) -> bool:
        return (
            self.claims.is_completed(self.room_id, msg.id)
            or self._retry_tracker.is_permanently_failed(msg.id)
            or bool(
                self._agent_id
                and is_self_echo(
                    sender_id=msg.sender_id or "",
                    sender_type=msg.sender_type or "",
                    agent_id=self._agent_id,
                )
            )
        )

    async def _process_backlog_message(
        self, msg: PlatformMessage, *, revision: int | None = None
    ) -> BacklogProcessResult:
        """
        Process a backlog message from /next during sync.

        Skips self-echoes, permanently failed and already completed messages,
        retries a locally settled message's terminal write, and otherwise runs
        it as a one-original attempt.
        """
        msg_id = msg.id

        # Skip messages from self (agent's own messages) to avoid infinite loops.
        # ``PlatformMessage`` declares these as ``str``, but it's a plain
        # dataclass filled from Fern models -- a backend null reaches here as
        # ``None`` and would otherwise raise ``TypeError`` from is_self_echo
        # (a pyo3 extension with non-Optional ``str`` params).
        if self._agent_id and is_self_echo(
            sender_id=msg.sender_id or "",
            sender_type=msg.sender_type or "",
            agent_id=self._agent_id,
        ):
            logger.debug("Skipping self-message %s", msg_id)
            return BacklogProcessResult.ADVANCED

        # Skip permanently failed messages
        if self._retry_tracker.is_permanently_failed(msg_id):
            logger.debug("Skipping permanently failed message %s", msg_id)
            return BacklogProcessResult.ADVANCED

        # Skip if already processed (dedupe)
        if self.claims.is_completed(self.room_id, msg_id):
            logger.debug("Skipping duplicate backlog message: %s", msg_id)
            return BacklogProcessResult.ADVANCED

        if self.claims.is_ack_pending(self.room_id, msg_id):
            logger.debug("Retrying terminal write for backlog message: %s", msg_id)
            if await self._retry_terminal_ack(msg_id):
                return BacklogProcessResult.ADVANCED
            return BacklogProcessResult.RETRY_LATER

        if self._delivery_status_for_agent(msg.metadata) == DeliveryStatus.PROCESSED:
            logger.info(
                "Skipping stale /next message %s in room %s because it is already processed",
                msg_id,
                self.room_id,
            )
            self.claims.remember_completed(self.room_id, msg_id)
            return BacklogProcessResult.ADVANCED

        return await self._run_message_attempt(
            (_backlog_event(msg),),
            revision=self._control_revision if revision is None else revision,
        )

    async def _run_message_attempt(
        self,
        events: tuple[MessageEvent, ...],
        *,
        revision: int,
        handler: Callable[[], Awaitable[bool]] | None = None,
    ) -> BacklogProcessResult:
        """Claim, admit, run and settle the originals of one attempt together."""
        ids = tuple(_event_message_id(event) for event in events)
        with self._claim_messages(ids) as claimed:
            if not claimed:
                logger.debug(
                    "ExecutionContext %s: Deferring owned message(s) %s",
                    self.room_id,
                    ids,
                )
                return BacklogProcessResult.RETRY_LATER
            return await self._run_claimed_attempt(events, revision, handler)

    async def _run_claimed_attempt(
        self,
        events: tuple[MessageEvent, ...],
        revision: int,
        handler: Callable[[], Awaitable[bool]] | None,
    ) -> BacklogProcessResult:
        """Run one attempt whose originals this execution has all claimed."""
        members = self._without_processed(events)
        if not members:
            return BacklogProcessResult.ADVANCED
        scope = await self._begin_scope()
        if scope is None or revision != self._control_revision:
            return BacklogProcessResult.RETRY_LATER
        self._set_state(ExecutionState.PROCESSING)
        logger.info("Processing %s message(s) in room %s", len(members), self.room_id)

        attempts: dict[str, int] = {}
        try:
            members = self._charge_attempts(members, attempts)
            if members and not self._context_hydrated:
                await self._ensure_fresh_context()
                members = self._without_processed(members, attempts)
            if not members:
                return BacklogProcessResult.ADVANCED
            if revision != self._control_revision:
                return BacklogProcessResult.RETRY_LATER
            scope.originals = members
            if not await self._confirm_processing(scope, revision):
                return BacklogProcessResult.RETRY_LATER
            scope.admitted = True
            return await self._execute_attempt(scope, revision, handler)

        except InputNotAccepted:
            self._discard_attempts(attempts)
            self._defer_unaccepted_input(_event_message_id(scope.originals[0]))
            return BacklogProcessResult.NOT_ACCEPTED

        except RoomExecutionStoppedError:
            self._discard_unsettled_attempts(attempts)
            return BacklogProcessResult.RETRY_LATER

        except Exception as e:  # noqa: BLE001 -- the turn boundary: logged by _log_turn_error, then marked failed
            _log_turn_error(e, "Error processing messages in room %s", self.room_id)
            await self._fail_attempt(scope, attempts, e)
            return BacklogProcessResult.ADVANCED

        finally:
            if not scope.admitted:
                self._discard_attempts(attempts)
            scope.settled = True
            self._turn_failure_reported = False
            self._set_state(ExecutionState.IDLE)

    async def _execute_attempt(
        self,
        scope: TurnScope,
        revision: int,
        handler: Callable[[], Awaitable[bool]] | None,
    ) -> BacklogProcessResult:
        """Run an admitted scope's handler once, then write its stored intents."""
        await self._ensure_fresh_context()
        if revision != self._control_revision:
            return await self._defer_superseded(scope)
        if not await self._run_cycle(scope, scope.originals[0], handler=handler):
            if scope.stop_observed:
                return BacklogProcessResult.RETRY_LATER
            return BacklogProcessResult.ADVANCED
        if not await self._settle_originals(scope):
            logger.warning(
                "ExecutionContext %s: Local execution completed but the durable "
                "terminal write is pending for %s message(s)",
                self.room_id,
                len(scope.originals),
            )
            return BacklogProcessResult.RETRY_LATER
        self._clear_deferred_head_for(scope.originals)
        return BacklogProcessResult.ADVANCED

    async def _confirm_processing(self, scope: TurnScope, revision: int) -> bool:
        """Mark every original processing in order; any refusal defers them all.

        The scope is admitted only after the last acceptance, so a control
        landing earlier consumes nothing.
        """
        for event in scope.originals:
            message_id = _event_message_id(event)
            try:
                accepted = await self.link.mark_processing(self.room_id, message_id)
            except RoomExecutionStoppedError:
                accepted = False
            if not accepted:
                logger.warning(
                    "ExecutionContext %s: Could not claim message %s",
                    self.room_id,
                    message_id,
                )
                return False
            if not self._owns_attempt(scope, revision):
                return False
        return True

    def _owns_attempt(self, scope: TurnScope, revision: int) -> bool:
        return (
            self.current_scope is scope
            and revision == self._control_revision
            and not scope.terminating
        )

    def _without_processed(
        self,
        events: tuple[MessageEvent, ...],
        attempts: dict[str, int] | None = None,
    ) -> tuple[MessageEvent, ...]:
        """Complete originals the platform already reports processed; return the rest."""
        pending: list[MessageEvent] = []
        for event in events:
            message_id = _event_message_id(event)
            if not self._message_processed_for_agent(
                message_id, event.payload.metadata if event.payload else None
            ):
                pending.append(event)
                continue
            logger.info(
                "Skipping processed replay message %s in room %s",
                message_id,
                self.room_id,
            )
            if attempts is not None and message_id in attempts:
                self._retry_tracker.discard_attempt(message_id)
                del attempts[message_id]
            self.claims.remember_completed(self.room_id, message_id)
        return tuple(pending)

    def _charge_attempts(
        self, events: tuple[MessageEvent, ...], attempts: dict[str, int]
    ) -> tuple[MessageEvent, ...]:
        """Charge one model attempt per original; an exhausted one is skipped."""
        chargeable: list[MessageEvent] = []
        for event in events:
            message_id = _event_message_id(event)
            count, exceeded = self._retry_tracker.record_attempt(message_id)
            if exceeded:
                logger.warning(
                    "Message %s exceeded max retries (%s attempts)", message_id, count
                )
                continue
            attempts[message_id] = count
            chargeable.append(event)
        return tuple(chargeable)

    def _discard_attempts(self, message_ids: Iterable[str]) -> None:
        for message_id in message_ids:
            self._retry_tracker.discard_attempt(message_id)

    def _discard_unsettled_attempts(self, message_ids: Iterable[str]) -> None:
        """Return the charge of every original without a retained terminal intent."""
        self._discard_attempts(
            message_id
            for message_id in message_ids
            if not self.claims.is_ack_pending(self.room_id, message_id)
        )

    def _clear_deferred_head_for(self, events: tuple[MessageEvent, ...]) -> None:
        if self._deferred_message_id in {_event_message_id(e) for e in events}:
            self._set_deferred_head(None)

    async def _defer_superseded(self, scope: TurnScope) -> BacklogProcessResult:
        """Unwind an admitted scope a control superseded before its handler ran."""
        if scope.kind is None:
            self._discard_attempts(_event_message_id(e) for e in scope.originals)
            return BacklogProcessResult.RETRY_LATER
        self._select_intent(scope, None, timed_out=False)
        await self._abort_cycle(scope.kind, scope, event=scope.originals[0])
        return BacklogProcessResult.ADVANCED

    async def _fail_attempt(
        self, scope: TurnScope, attempts: dict[str, int], error: Exception
    ) -> None:
        """Fail every original of an attempt whose handler raised, reporting once."""
        if self.config.consume_all_queued and scope.admitted and not scope.settled:
            self._settle_scope(scope, DeliveryStatus.FAILED)
        if scope.intent is DeliveryStatus.FAILED:
            await self._settle_originals(scope)
            await self._report_turn_failure(final=True)
            return
        for message_id, count in attempts.items():
            await self._handle_turn_failure(message_id, count, error)

    def _drain_duplicate_from_queue(self, msg_id: str) -> None:
        """
        Remove duplicate message from WebSocket queue after sync point reached.

        The message at sync point exists in both /next and WS queue.
        """
        # Drain queue and re-add non-duplicates
        items = []
        while not self.queue.empty():
            try:
                event = self.queue.get_nowait()
                if (
                    isinstance(event, MessageEvent)
                    and event.payload
                    and event.payload.id == msg_id
                ):
                    logger.debug("Removed duplicate from WS queue: %s", msg_id)
                    continue
                items.append(event)
            except asyncio.QueueEmpty:
                break

        # Re-add non-duplicates
        for item in items:
            self.queue.put_nowait(item)

    async def _invoke_handler(self, event: PlatformEvent) -> None:
        """Coroutine wrapper around the execution handler so it can run as a
        cancellable ``asyncio.Task`` (the handler is typed ``Awaitable``).

        Message-driven cycles start the working-state signal in
        ``_execute_message_cycle``, and ``_run_cycle`` stops it after the
        handler drains; participant add/remove events are housekeeping and
        skip it.
        """
        if isinstance(event, MessageEvent):
            await self._execute_message_cycle(event)
        else:
            await self._on_execute(self, event)

    async def _report_working_state(self, working: bool) -> bool:
        """Report the room's boolean working state (wired into the reporter)."""
        return await self.link.report_activity(
            self.room_id,
            working,
            timeout_seconds=self.config.working_request_timeout_seconds,
            on_stopped=partial(
                self._observe_activity_stop, self.current_scope, self._control_revision
            ),
        )

    def _observe_activity_stop(self, scope: TurnScope | None, revision: int) -> None:
        if (
            scope is None
            or not self._owns_control_revision(scope, revision)
            or scope.stop_observed
            or self._closing
            or scope.kind is not None
        ):
            return
        logger.info(
            "Activity report observed stopped execution for room %s", self.room_id
        )
        self.observe_platform_stop(scope, control_revision=revision)

    async def _execute_message_cycle(self, event: PlatformEvent) -> None:
        await self._working_reporter.start()
        scope = self.current_scope
        if scope is not None and scope.terminating:
            raise asyncio.CancelledError
        self._resync_handled_work = True
        self._reset_resync_interval()
        await self._on_execute(self, event)

    def _retain_detached_cycle_task(
        self, task: asyncio.Task[None], *, owner: TurnTask | None = None
    ) -> None:
        """Keep a task that outlived its grace referenced until it finishes."""
        if task in self._detached_cycle_tasks:
            return
        self._detached_cycle_tasks[task] = owner or TurnTask(task)
        task.add_done_callback(self._finish_detached_cycle_task)

    def _finish_detached_cycle_task(self, task: asyncio.Task[None]) -> None:
        """Release a retained task and report any late failure."""
        self._detached_cycle_tasks.pop(task, None)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error(
                "ExecutionContext %s: retained task failed",
                self.room_id,
                exc_info=error,
            )

    async def _drain_or_retain(self, owner: TurnTask) -> None:
        """Drain a terminated task, retaining it if cleanup outlives its grace."""
        try:
            drained = await owner.drain()
        except asyncio.CancelledError:
            # A cancelled drainer must not lose the task it was waiting on.
            self._retain_detached_cycle_task(owner.task, owner=owner)
            raise
        if not drained:
            logger.warning(
                "ExecutionContext %s: cancelled task outlived its cleanup grace; "
                "retaining it",
                self.room_id,
            )
            self._retain_detached_cycle_task(owner.task, owner=owner)
        elif not owner.task.cancelled() and (error := owner.task.exception()):
            logger.error(
                "ExecutionContext %s: cancelled task cleanup failed",
                self.room_id,
                exc_info=error,
            )

    async def _run_cycle(
        self,
        scope: TurnScope,
        event: PlatformEvent,
        *,
        handler: Callable[[], Awaitable[bool]] | None = None,
    ) -> bool:
        """Supervise one handler, then select its delivery operation once.

        The scope's terminal intent is stored at the synchronous point where the
        handler has finished or been drained, before any later cleanup or
        acknowledgement await.
        """
        if scope.kind is not None:
            self._select_intent(scope, None, timed_out=False)
            return await self._abort_cycle(scope.kind, scope, event=event)
        scope.admitted = True

        async def invoke() -> None:
            if handler is not None:
                scope.consumed = await handler()
            else:
                await self._invoke_handler(event)

        owner = TurnTask(asyncio.create_task(invoke()))
        scope.handler = owner
        wake = asyncio.create_task(scope.terminated.wait())
        timed_out = False
        try:
            done, _ = await asyncio.wait(
                {owner.task, wake},
                timeout=self.config.max_cycle_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done and not owner.task.done():
                timed_out = True
                scope.terminate()
            if scope.terminating:
                await self._drain_or_retain(owner)
            self._select_intent(scope, owner, timed_out=timed_out)
            if not scope.terminating:
                owner.task.result()
        except asyncio.CancelledError:
            scope.terminate(shutdown=True)
            await self._drain_or_retain(owner)
            raise
        finally:
            wake.cancel()
            await asyncio.gather(wake, return_exceptions=True)
            if isinstance(event, MessageEvent) and handler is None:
                # stop() joins this cycle inside the settlement budget, which is
                # shorter than the activity POST. A closing execution finishes
                # working:false after that join.
                if self._closing:
                    self._working_reporter.defer_false_report()
                else:
                    await self._working_reporter.stop()
            scope.settled = True
        if scope.shutdown:
            raise asyncio.CancelledError
        if scope.kind is not None:
            return await self._abort_cycle(scope.kind, scope, event=event)
        if timed_out:
            raise TimeoutError(
                f"cycle exceeded max_cycle_seconds={self.config.max_cycle_seconds}"
            )
        owner.task.result()
        return True

    def _select_intent(
        self, scope: TurnScope, owner: TurnTask | None, *, timed_out: bool
    ) -> None:
        """Choose, before any further await, the write the scope's originals owe.

        Shutdown, STOP and a handler that refused or was stopped leave them
        recoverable; an accepted INTERRUPT consumes them; otherwise the
        handler's own result decides. A retried singleton keeps its own
        failure policy, so only batching retains a failed intent.
        """
        match scope.kind:
            case ControlMode.STOP:
                intent = None
            case ControlMode.INTERRUPT:
                intent = DeliveryStatus.PROCESSED
            case _ if scope.shutdown:
                intent = None
            case _ if timed_out:
                intent = DeliveryStatus.FAILED
            case _:
                intent = _handler_status(owner)
        if intent is DeliveryStatus.FAILED and not self.config.consume_all_queued:
            intent = None
        if intent is DeliveryStatus.PROCESSED and not scope.consumed:
            intent = None
        self._settle_scope(scope, intent)

    def _settle_scope(self, scope: TurnScope, intent: DeliveryStatus | None) -> None:
        """Fix the scope's outcome and retain each original's terminal intent."""
        scope.settled = True
        scope.intent = intent
        for event in scope.originals:
            message_id = _event_message_id(event)
            match intent:
                case DeliveryStatus.PROCESSED:
                    self.claims.remember_ack_pending(self.room_id, message_id)
                case DeliveryStatus.FAILED:
                    self.claims.remember_failed_ack_pending(
                        self.room_id, message_id, GENERIC_PROVIDER_FAILURE_MESSAGE
                    )

    async def claim_message(self, message_id: str) -> bool:
        """Claim an auxiliary message from directly awaited active preprocessing.

        The host owns local claims and acknowledgements. Ordinary refusal returns
        False; stopped execution cancels this turn and leaves it replayable.
        Calls outside the active handler raise RuntimeError before making a request.
        """
        scope = self.current_scope
        task = asyncio.current_task()
        if scope is None or task is None or task is not self._active_cycle_task:
            raise RuntimeError("claim_message requires the active handler task")
        if scope.terminating or task.cancelling():
            raise asyncio.CancelledError

        try:
            accepted = await self.link.mark_processing(self.room_id, message_id)
        except RoomExecutionStoppedError as error:
            if self.current_scope is scope and self._active_cycle_task is task:
                self.observe_platform_stop(scope, cooperative=True)
            raise asyncio.CancelledError from error

        # A transport can suppress cancellation and return into an obsolete turn.
        if (
            self.current_scope is not scope
            or self._active_cycle_task is not task
            or task.cancelling()
            or scope.terminating
        ):
            raise asyncio.CancelledError
        return accepted

    async def _abort_cycle(
        self,
        kind: ControlMode,
        scope: TurnScope,
        *,
        event: PlatformEvent | None = None,
    ) -> bool:
        """Unwind an aborted cycle (interrupt/stop): drop work, send nothing.

        Used by ``_run_cycle`` for a scope terminated by STOP/INTERRUPT after
        its admission. Returns False so the caller sends nothing further.
        ``kind`` is only ever ``INTERRUPT``/``STOP`` here —
        ``interrupt()`` rejects ``PLAY`` before either path can reach this.
        """
        # The handler never ran to completion, so uncharge the attempt
        # `record_attempt` already billed before this cycle started — otherwise
        # a message that's merely stopped/interrupted a couple of times gets
        # poisoned into permanently_failed before it's ever actually attempted.
        self._discard_attempts(_event_message_id(e) for e in scope.originals)
        await self._clear_activity()
        if kind is ControlMode.STOP:
            self._retain_synthetic(event, first=True)
        # Consume an interrupted message so the idle /next resync does not
        # re-return it and re-fire the cycle the user just interrupted. A
        # failed write stays retained as a pending intent.
        if (
            scope.intent is DeliveryStatus.PROCESSED
            and not await self._settle_interrupted(scope)
        ):
            logger.warning(
                "ExecutionContext %s: durable terminal write failed for "
                "interrupted message(s); retrying in background",
                self.room_id,
            )
        # For "stop" we deliberately leave the message in 'processing' so the
        # platform replays it via /next on play; do not mark or remember.
        #
        # CROSS-SYSTEM INVARIANT: this replay depends on the platform's /next
        # (Chat.get_next_actionable_message) excluding ONLY 'processed' — a
        # 'processing' message must still be returned. If the platform ever also
        # excludes 'processing', stopped messages are silently dropped on play.
        # Guarded live by the /next-actionable-semantics baseline E2E; the unit
        # replay test mocks /next and so cannot cover this cross-system half.
        logger.info(
            "ExecutionContext %s: cycle %s (%s message(s)) — nothing sent",
            self.room_id,
            "interrupted" if kind is ControlMode.INTERRUPT else "stopped",
            len(scope.originals),
        )
        return False

    async def _settle_interrupted(self, scope: TurnScope) -> bool:
        try:
            return await self._settle_originals(scope)
        except RoomExecutionStoppedError:
            return False

    async def _process_event(self, event: PlatformEvent) -> bool:
        """
        Process single event through execution callback.

        For message events, handles full lifecycle:
        1. Check if permanently failed or duplicate
        2. Record attempt with retry tracker
        3. Mark as processing on server
        4. Execute handler
        5. Mark as processed (success) or failed (exception)
        """
        if isinstance(event, ReconnectedEvent):
            await self._reconcile()
            return True

        # While stopped, leave message triggers actionable for replay on play.
        # Suppression is platform-authoritative; skipping here is a local
        # efficiency short-circuit that avoids claiming/marking (mark->204) and
        # never reaches the adapter (reply->403). The message is left untouched
        # so /next replays it on play.
        if self._stopped and isinstance(event, MessageEvent):
            self._retain_synthetic(event)
            logger.debug(
                "ExecutionContext %s: stopped, skipping message %s (left for replay)",
                self.room_id,
                event.payload.id if event.payload else None,
            )
            return True

        payload = event.payload if isinstance(event, MessageEvent) else None
        msg_id = payload.id if payload else None

        # For messages: check if we should skip
        if isinstance(event, MessageEvent) and msg_id and payload:
            # Skip messages from self (agent's own messages) to avoid infinite
            # loops. ``payload`` is hydrated via ``model_construct``, which
            # bypasses pydantic validation -- a null sender field would
            # otherwise raise ``TypeError`` from is_self_echo's non-Optional
            # ``str`` params.
            if self._agent_id and is_self_echo(
                sender_id=payload.sender_id or "",
                sender_type=payload.sender_type or "",
                agent_id=self._agent_id,
            ):
                logger.debug("Skipping self-message %s", msg_id)
                return True

            # Detect synthetic messages (e.g., contact events injected into hub room)
            # These don't exist in the database, so skip all tracking and marking
            is_synthetic = is_contact_hub_turn(
                sender_type=payload.sender_type, sender_id=payload.sender_id
            )
            if is_synthetic:
                logger.debug("Processing synthetic contact event message")
                msg_id = None  # Clear to skip message marking later
                # Skip all tracking for synthetic messages - go directly to processing
            else:
                # Only track retries and duplicates for real messages
                # Skip permanently failed messages
                if self._retry_tracker.is_permanently_failed(msg_id):
                    logger.debug("Skipping permanently failed message %s", msg_id)
                    return True

                # Skip duplicates
                if self.claims.is_completed(self.room_id, msg_id):
                    logger.debug("Skipping duplicate message %s", msg_id)
                    return True

                if self.claims.is_ack_pending(self.room_id, msg_id):
                    logger.debug("Retrying terminal write for message %s", msg_id)
                    return bool(await self._retry_terminal_ack(msg_id))

                # An owned or refused message stays unfinished: the resync
                # safety net re-checks deferred work, so it is never lost.
                outcome = await self._run_message_attempt(
                    (event,), revision=self._control_revision
                )
                return outcome is BacklogProcessResult.ADVANCED

        processed = await self._process_event_body(event)
        if not processed:
            self._retain_synthetic(event, first=True)
        return processed

    def note_turn_failure_reported(self) -> None:
        """Record that the adapter already told the room this turn failed."""
        self._turn_failure_reported = True

    async def _handle_turn_failure(
        self,
        msg_id: str,
        attempts: int | None,
        error: Exception,
    ) -> None:
        """Mark a failed turn failed and, on its final attempt, report it to
        the room as an ``error`` event.

        An event mentions no one and is never delivered as a turn, so it is
        safe for agent senders too. ``attempts`` is None when the failure
        preceded ``record_attempt``, which can't be judged final.
        """
        try:
            if not await self.link.mark_failed(
                self.room_id, msg_id, _error_label(error)
            ):
                logger.warning(
                    "ExecutionContext %s: Failed to mark message %s as failed",
                    self.room_id,
                    msg_id,
                )
        except RoomExecutionStoppedError:
            logger.debug(
                "ExecutionContext %s: failure marking refused while stopped",
                self.room_id,
            )
            return
        await self._report_turn_failure(
            final=attempts is not None and attempts >= self._retry_tracker.max_retries
        )

    async def _report_turn_failure(self, *, final: bool) -> None:
        """Tell the room a turn failed, once, unless the adapter already did."""
        # Only a post that landed counts: an adapter raises
        # TurnResultAlreadyReported even when its send_failure did not post.
        already_reported = self._turn_failure_reported
        if not (
            self.config.report_turn_failures_to_room and final and not already_reported
        ):
            logger.debug(
                "ExecutionContext %s: No turn-failure report "
                "(enabled=%s, final=%s, already_reported=%s)",
                self.room_id,
                self.config.report_turn_failures_to_room,
                final,
                already_reported,
            )
            return
        # The exception text can carry credentials; it stays in mark_failed
        # and the logs, and the room gets the generic message.
        try:
            await AgentTools.from_context(self, bind_scope=False).send_failure(
                AgentFailure(TURN_FAILURE_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)
            )
        except RoomExecutionStoppedError:
            logger.debug(
                "ExecutionContext %s: failure reporting refused while stopped",
                self.room_id,
            )

    async def _process_event_body(self, event: PlatformEvent) -> bool:
        """Run an event that owes no delivery write: housekeeping or a synthetic message."""
        scope = await self._begin_scope()
        if scope is None:
            return False

        self._set_state(ExecutionState.PROCESSING)
        logger.debug("Processing %s in room %s", event.type, self.room_id)

        try:
            # Hydrate context on first event (loads participants always,
            # history only if enable_context_hydration is True)
            await self._ensure_fresh_context()

            # Handle participant events internally; the callback below still
            # fires either way since it reports the platform event, not roster state.
            if isinstance(event, ParticipantAddedEvent) and event.payload:
                payload = event.payload
                log_roster_call(
                    logger,
                    call=self.add_participant,
                    arg=payload.model_dump(),
                    room_id=self.room_id,
                )
                await self._notify_participant_added(event)
            elif isinstance(event, ParticipantRemovedEvent) and event.payload:
                payload = event.payload
                log_roster_call(
                    logger,
                    call=self.remove_participant,
                    arg=payload.id,
                    room_id=self.room_id,
                )
                await self._notify_participant_removed(event)

            # Call execution handler as a cancellable cycle. A control signal
            # can abort just this turn; when it does, status is handled inside
            # _run_cycle and we send nothing further. Only message-driven
            # cycles report the working signal (see _invoke_handler);
            # participant add/remove events are housekeeping and skip it.
            if not await self._run_cycle(scope, event):
                return not scope.stop_observed

            logger.debug("Event %s processed successfully", event.type)
            return True

        except InputNotAccepted:
            self._defer_unaccepted_input(None)
            return False

        except RoomExecutionStoppedError:
            return False

        except Exception as e:  # noqa: BLE001 -- the turn boundary: logged by _log_turn_error, then marked failed
            _log_turn_error(e, "Error processing %s", event.type)
            return True

        finally:
            scope.settled = True
            self._turn_failure_reported = False
            self._set_state(ExecutionState.IDLE)


def _backlog_event(msg: PlatformMessage) -> MessageEvent:
    created_at_str = (
        msg.created_at.isoformat() if msg.created_at else datetime.now(UTC).isoformat()
    )

    # REST mentions may use name; the WebSocket projection requires username.
    metadata = metadata_to_dict(msg.metadata).copy()
    if "mentions" in metadata:
        normalized_mentions = []
        for mention in metadata.get("mentions", []):
            if isinstance(mention, dict):
                normalized_mentions.append(
                    {
                        "id": mention.get("id", ""),
                        "username": mention.get("username")
                        or mention.get("name")
                        or mention.get("id", ""),
                    }
                )
        metadata["mentions"] = normalized_mentions
    else:
        metadata["mentions"] = []

    if "status" not in metadata:
        metadata["status"] = "sent"

    return MessageEvent(
        room_id=msg.room_id,
        payload=MessageCreatedPayload(
            id=msg.id,
            content=msg.content,
            sender_id=msg.sender_id,
            sender_type=msg.sender_type,
            message_type=msg.message_type,
            metadata=MessageMetadata(**metadata),
            chat_room_id=msg.room_id,
            inserted_at=created_at_str,
            updated_at=created_at_str,
        ),
    )
