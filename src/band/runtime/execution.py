"""Room scheduling, context and ownership of asynchronous turn effects."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from enum import Enum, StrEnum
from functools import partial
from typing import (
    TYPE_CHECKING,
    Any,
    Protocol,
    TypeGuard,
    runtime_checkable,
)

from band_sdk_core import (
    AgentFailure,
    ClaimRegistry,
    ParticipantRoster,
    RetryTracker,
)

from band.client.rest import DEFAULT_REQUEST_OPTIONS
from band.client.streaming import (
    ControlMode,
    DeliveryStatus,
    MessageCreatedPayload,
    MessageMetadata,
)
from band.core.exceptions import (
    BandConfigError,
    CleanupIncompleteError,
    InputNotAccepted,
    RoomExecutionStoppedError,
)
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TURN_FAILURE_PROVIDER,
)
from band.core.retention import task_failure
from band.core.turn_lifecycle import (
    TurnTask,
    finished_within_grace,
    terminal_deadline,
)
from band.core.types import (
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
from band.runtime.callbacks import ParticipantAddedCallback, ParticipantRemovedCallback
from band.runtime.context import ConversationContext
from band.runtime.context_serialization import context_item_to_dict
from band.runtime.cycle import TurnScope
from band.runtime.delivery import (
    DEFERRED_OUTCOMES,
    ControlOutcome,
    DeliveryLifecycle,
    TurnOutcome,
    log_delivery_error,
    observe_delivery_phase,
)
from band.runtime.formatters import build_participants_message, format_history_for_llm
from band.runtime.messages import PlatformMessage
from band.runtime.participants import log_roster_call, log_roster_error
from band.runtime.resync import ResyncSchedule
from band.runtime.selection import (
    Candidate,
    Disposition,
    MessageKey,
    Selection,
    Stop,
    select_prefix,
)
from band.runtime.sessionconfig import SessionConfig
from band.runtime.tools.agent import AgentTools
from band.runtime.working_state import WorkingStateReporter

if TYPE_CHECKING:
    from band.platform.link import BandLink

logger = logging.getLogger(__name__)


class ResyncRequest:
    """Request reconciliation at the next serial queue boundary."""

    type: str = "_resync"  # Matches the .type attribute pattern of PlatformEvent


class ResyncOutcome(Enum):
    EMPTY = "empty"
    WORK = "work"
    BLOCKED = "blocked"
    RETRY = "retry"
    STOPPED = "stopped"
    PROGRESS = "progress"
    """A pass advanced the room and another is due; a drain never returns it."""


class ExecutionState(StrEnum):
    """Lifecycle state for one room execution."""

    STARTING = "starting"
    IDLE = "idle"
    PROCESSING = "processing"


def _payload(event: MessageEvent) -> MessageCreatedPayload:
    """The payload of a message event that carries a real delivery."""
    if event.payload is None:
        raise ValueError("a message event without a payload has no delivery")
    return event.payload


def _event_message_id(event: MessageEvent) -> str:
    """The platform id of a message event that carries a real delivery."""
    return _payload(event).id


def _message_ids(events: Iterable[MessageEvent]) -> tuple[str, ...]:
    return tuple(_event_message_id(event) for event in events)


def _delivery_id(event: PlatformEvent) -> str | None:
    """The platform id of a message delivery; ``None`` for any other event."""
    if isinstance(event, MessageEvent) and event.payload is not None:
        return event.payload.id
    return None


def _is_ordinary(event: PlatformEvent) -> TypeGuard[MessageEvent]:
    """Whether the event is a real delivery a turn can include."""
    return _delivery_id(event) is not None and not _is_synthetic(event)


def _is_synthetic(event: PlatformEvent) -> TypeGuard[MessageEvent]:
    """Whether the event is a contact-hub turn that owes no delivery write."""
    return (
        isinstance(event, MessageEvent)
        and event.payload is not None
        and is_contact_hub_turn(
            sender_type=event.payload.sender_type, sender_id=event.payload.sender_id
        )
    )


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
BatchExecutionHandler = Callable[
    ["ExecutionContext", tuple[MessageEvent, ...]], Awaitable[None]
]


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
        on_execute_batch: BatchExecutionHandler | None = None,
        is_message_control: Callable[[ExecutionContext, MessageEvent], bool]
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
            on_execute_batch: Callback running one turn over several originals.
                Required when ``config.consume_all_queued`` is set.
            is_message_control: Read-only recognition of a delivery the
                adapter would handle as a control. Without it controls are
                handled in ordinary queue order.

        Raises:
            BandConfigError: ``config.consume_all_queued`` without a batch host.
        """
        self.config = config or SessionConfig()
        if self.config.consume_all_queued and on_execute_batch is None:
            raise BandConfigError(
                "consume_all_queued needs a host that runs a turn over several "
                "originals (on_execute_batch)"
            )
        self.room_id = room_id
        self.link = link
        self._on_execute = on_execute
        self._on_execute_batch = on_execute_batch
        self._on_message_control = on_message_control
        self._is_message_control = is_message_control
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
        # Loop time before which an unconfirmed terminal write is not retried, so
        # arrivals cannot multiply an outage's writes. A due or requested
        # reconciliation clears it.
        self.delivery = DeliveryLifecycle(
            self.room_id,
            self.link,
            self.claims,
            self._retry_tracker,
            self.config,
            self,
            self._agent_id,
        )
        self._sync_complete = False  # True after sync with /next completes
        self._deferred_message_id: str | None = None
        # Retained local events in observed order: synthetic hub turns, original
        # deliveries not yet settled, and (batch mode) every housekeeping event.
        self._pending: deque[PlatformEvent] = deque()
        self._scan_cursor: str | None = None
        self._last_selection: tuple[str, ...] = ()
        self._control_cursor: str | None = None

        # Graceful shutdown: event signaled when state becomes idle
        self._idle_event: asyncio.Event = asyncio.Event()
        self._idle_event.set()  # Start as idle

        # Pending system messages to inject (e.g., contact broadcasts)
        self._pending_system_messages: list[str] = []
        self._pending_resync: ResyncRequest | None = None
        self._resync_schedule = ResyncSchedule(
            self.config.idle_resync_seconds,
            self.config.idle_resync_max_seconds,
            stopped=self.config.stopped_resync_seconds,
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

        # Received STOP suppresses local triggers. Periodic probes discover
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

    def delivery_metadata(self, message_id: str) -> dict[str, Any] | None:
        """Return metadata for a hydrated context message by ID."""
        if not self._context_cache:
            return None

        for message in self._context_cache.messages:
            if message.get("id") == message_id:
                return metadata_to_dict(message.get("metadata"))

        return None

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
            # A released worker belongs to adapter room cleanup, not shutdown; an
            # attempt still being admitted is not released yet and must not run.
            scope = self.current_scope
            if scope is not None and not scope.settled:
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
        self.delivery.retry_not_before.clear()
        if self._pending_resync is not None:
            return
        self._pending_resync = ResyncRequest()
        self.queue.put_nowait(self._pending_resync)  # type: ignore[arg-type]  # The sentinel only wakes this loop.
        logger.debug("ExecutionContext %s: Resync sentinel enqueued", self.room_id)

    def interrupt(self, *, kind: ControlMode | str = ControlMode.INTERRUPT) -> bool:
        """Abort the in-flight reasoning cycle, if any.

        Called from the WebSocket receive task. It only terminates the running
        turn scope; all message-status / dedupe bookkeeping happens once in
        ``execute_delivery``, never here, so the two coroutines do not race on shared
        state.

        Args:
            kind: ``ControlMode.INTERRUPT`` (consume the message) or
                ``ControlMode.STOP`` (leave it actionable for replay on play).
                Distinguishes the two unwind paths in ``execute_delivery``. A plain
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
        self._resync_schedule.reset(
            asyncio.get_running_loop().time(), stopped=self._stopped
        )
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

    @property
    def control_revision(self) -> int:
        return self._control_revision

    @property
    def is_closing(self) -> bool:
        return self._closing

    @property
    def context_hydrated(self) -> bool:
        return self._context_hydrated

    def owns_delivery(self, scope: TurnScope, revision: int) -> bool:
        return (
            not self._closing
            and self._owns_control_revision(scope, revision)
            and not scope.terminating
        )

    def finish_delivery(self, scope: TurnScope) -> None:
        scope.settle()
        self._turn_failure_reported = False
        self._set_state(ExecutionState.IDLE)

    async def begin_delivery(self) -> TurnScope | None:
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
        self._set_state(ExecutionState.PROCESSING)
        return self.current_scope

    async def _begin_turn(self, revision: int | None = None) -> TurnScope | None:
        """Begin a message turn that controls terminate from this point on."""
        if revision is None:
            revision = self._control_revision
        scope = await self.begin_delivery()
        if scope is None:
            return None
        if revision != self._control_revision:
            logger.debug(
                "ExecutionContext %s: control changed while admitting the turn; deferring",
                self.room_id,
            )
            self.finish_delivery(scope)
            return None
        scope.admit()
        return scope

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

    async def prepare_delivery_context(self, *, force_refresh: bool = False) -> None:
        """Hydrate context if missing, expired, or explicitly refreshed."""
        if force_refresh:
            self._invalidate_context_cache()
        else:
            self._expire_context_cache_if_needed()

        if not self._context_hydrated:
            with observe_delivery_phase("hydrate", self.room_id):
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
        await self.prepare_delivery_context(force_refresh=force_refresh)

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
            if self._sync_complete and (
                self.config.consume_all_queued or self._pending
            ):
                await self._reconcile()

            self._set_state(ExecutionState.IDLE)
            logger.info(
                "ExecutionContext %s: Startup pass finished; servicing queue and recovery",
                self.room_id,
            )

            loop = asyncio.get_running_loop()
            self._resync_schedule.start(loop.time())
            while not self._closing:
                deadline = self._resync_schedule.due_at
                if deadline is not None and deadline <= loop.time():
                    self.delivery.retry_not_before.clear()
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
                if self.config.consume_all_queued:
                    self._retain_arrival(event)
                    await self._service_arrival()
                    continue
                if isinstance(event, MessageEvent) and (
                    not self._sync_complete or self._has_pending_work
                ):
                    await self._retain_or_handle_control(event)
                    continue
                if self._enqueue_synthetic(event):
                    await self._drain_synthetics()
                    continue
                if (
                    await self._process_event(event) in DEFERRED_OUTCOMES
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
        return self._ordinary_work_deferred or self._has_synthetic

    @property
    def _ordinary_work_deferred(self) -> bool:
        return (
            self._deferred_message_id is not None
            or self.claims.has_retryable_settlement(self.room_id)
        )

    def _set_deferred_head(self, message_id: str | None) -> None:
        if message_id != self._deferred_message_id:
            self._deferred_message_id = message_id
            self._control_cursor = None

    def defer_delivery(
        self, message_id: str | None, *, reason: InputNotAccepted
    ) -> None:
        if message_id:
            self._set_deferred_head(message_id)
        logger.debug(
            "ExecutionContext %s: Input %s not accepted (%s); "
            "keeping it pending for scheduler recovery",
            self.room_id,
            message_id or "synthetic",
            reason,
        )

    def _retain(self, event: PlatformEvent, *, first: bool = False) -> None:
        """Keep a message delivery's payload until its lifecycle settles it."""
        delivery_id = _delivery_id(event)
        if delivery_id is None:
            return
        if any(_delivery_id(item) == delivery_id for item in self._pending):
            return
        if first:
            self._pending.appendleft(event)
        else:
            self._pending.append(event)

    def _release(self, message_ids: Iterable[str]) -> None:
        released = set(message_ids)
        self._pending = deque(
            event for event in self._pending if _delivery_id(event) not in released
        )

    async def _retain_or_handle_control(self, event: MessageEvent) -> None:
        self._retain(event)
        if _is_synthetic(event):
            return
        if self._stopped or self._on_message_control is None:
            return
        await self._scan_controls()
        # A control may have settled the deferred head the input waited behind.
        if self._sync_complete and not self._has_pending_work and not self._stopped:
            await self._reconcile()

    async def _scan_controls(self) -> None:
        """Offer each candidate, retained originals included, to control handling."""
        if self._on_message_control is None:
            return
        revision = self._control_revision
        try:
            page = await self.link.get_actionable_page(
                self.room_id, cursor=self._control_cursor
            )
            if revision != self._control_revision:
                return
            for event in self._control_candidates(page.messages):
                if self._stopped or revision != self._control_revision:
                    return
                await self._try_control(event)
            self._control_cursor = page.next_cursor
        except RoomExecutionStoppedError:
            return
        except Exception:
            logger.exception("Control recovery failed for room %s", self.room_id)

    def _control_candidates(
        self, messages: Iterable[PlatformMessage]
    ) -> list[MessageEvent]:
        """REST candidates and retained originals, once each, oldest first.

        A retained payload wins over its REST copy; the deferred head is
        included because a pending question may now make it a control.
        """
        events = {_event_message_id(e): e for e in map(_backlog_event, messages)}
        for event in self._pending:
            if _is_ordinary(event):
                events[_event_message_id(event)] = event
        return sorted(events.values(), key=MessageKey.of_event)

    async def _try_control(self, event: MessageEvent) -> bool:
        return await self._probe_control(event) is ControlOutcome.CONSUMED

    def recognizes_delivery_control(self, event: MessageEvent) -> bool:
        return self._is_message_control is not None and self._is_message_control(
            self, event
        )

    async def _probe_control(self, event: MessageEvent) -> ControlOutcome:
        if self._on_message_control is None or event.payload is None:
            return ControlOutcome.DECLINED
        if not self.recognizes_delivery_control(event):
            return ControlOutcome.DECLINED
        outcome = await self.delivery.run(
            (event,), self._control_revision, control=True
        )
        if outcome is TurnOutcome.ADVANCED:
            return self._settled_control(event)
        if outcome is TurnOutcome.DECLINED:
            return ControlOutcome.DECLINED
        return ControlOutcome.DEFERRED

    def _settled_control(self, event: MessageEvent) -> ControlOutcome:
        """A settled control leaves the line and no longer heads it."""
        self._release((_event_message_id(event),))
        self._clear_deferred_head_for((event,))
        return ControlOutcome.CONSUMED

    async def _retry_pending_terminal_acks(self) -> bool:
        """Retry every unparked pending write once; False while any stays retryable."""
        resolved = [
            await self.delivery.retry_settlement(message_id)
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
            empty=outcome is ResyncOutcome.EMPTY,
            stopped=self._stopped,
        )

    async def _wait_until_resync_complete(self) -> ResyncOutcome:
        """Preserve recovery ordering while allowing stopped-room discovery."""
        candidate: PlatformMessage | None = None
        if self._stopped:
            revision = self._control_revision
            try:
                candidate = await self._get_next_message()
            except Exception:
                logger.exception("Stopped-room probe failed for %s", self.room_id)
                return ResyncOutcome.RETRY
            if revision != self._control_revision:
                return ResyncOutcome.BLOCKED
            if candidate is None:
                return ResyncOutcome.STOPPED
            if self._is_nonrunnable(candidate):
                return ResyncOutcome.STOPPED
            self._resume_room()

        if await self._retry_pending_terminal_acks():
            if self._stopped:
                return ResyncOutcome.STOPPED
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
        if self.config.consume_all_queued:
            # The selector reads the actionable list and merges retained
            # originals by id, so there is no sync point to find.
            self._sync_complete = True
            return True
        logger.debug(
            "ExecutionContext %s: Starting /next synchronization", self.room_id
        )

        previous_id: str | None = None
        try:
            while not self._closing:
                revision = self._control_revision
                next_msg = await self._get_next_message()
                if revision != self._control_revision:
                    if self._stopped:
                        return True
                    continue
                if (
                    next_msg is not None
                    and next_msg.id == previous_id
                    and self._is_nonrunnable(next_msg)
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
                if result in DEFERRED_OUTCOMES:
                    self._set_deferred_head(next_msg.id)
                    return False
                if at_sync_point:
                    # Preserve other queued events when removing the sync point.
                    self._drain_duplicate_from_queue(next_msg.id)
                    self._first_ws_msg_id = None
                    self._sync_complete = True
                    return True

                if self._stopped or result is TurnOutcome.PAUSED:
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

        except Exception:
            logger.exception("ExecutionContext %s: Sync error", self.room_id)
            return False

        if self._closing:
            return False
        logger.debug("ExecutionContext %s: Synchronization complete", self.room_id)
        self._sync_complete = True
        return True

    async def _get_next_message(self) -> PlatformMessage | None:
        """Use /next's room gate, then read past locally excluded originals."""
        next_message = await self.link.get_next_message(self.room_id)
        if next_message is None or not self._is_nonrunnable(next_message):
            return next_message
        cursor: str | None = None
        while True:
            page = await self.link.get_actionable_page(self.room_id, cursor=cursor)
            for message in page.messages:
                if not self._is_nonrunnable(message):
                    return message
            if page.next_cursor is None:
                return None
            cursor = page.next_cursor

    async def _resync_pending_messages(
        self, candidate: PlatformMessage | None = None
    ) -> ResyncOutcome:
        """Drain actionable work, distinguishing empty from blocked recovery."""
        self._resync_handled_work = False
        if self.config.consume_all_queued:
            return await self._drain_pending()
        if (
            self._deferred_message_id is None
            and (outcome := await self._drain_synthetics()) is not None
        ):
            return outcome
        previous_id: str | None = None
        try:
            while not self._closing:
                revision = self._control_revision
                next_msg = candidate
                candidate = None
                if next_msg is None:
                    next_msg = await self._get_next_message()
                if revision != self._control_revision:
                    # A STOP ends the pass; an INTERRUPT leaves the room running,
                    # so the read it overtook is repeated.
                    if self._stopped:
                        return ResyncOutcome.BLOCKED
                    continue
                if (retained := self._retained_before(next_msg)) is not None:
                    candidate = next_msg
                    match await self._process_event(retained):
                        case TurnOutcome.ADVANCED:
                            pass
                        case TurnOutcome.PAUSED:
                            return ResyncOutcome.STOPPED
                        case _:
                            return ResyncOutcome.BLOCKED
                    if self._stopped:
                        return ResyncOutcome.STOPPED
                    continue
                if next_msg is None:
                    self._set_deferred_head(None)
                    if self._has_synthetic:
                        return ResyncOutcome.BLOCKED
                    return (
                        ResyncOutcome.WORK
                        if self._resync_handled_work
                        else ResyncOutcome.EMPTY
                    )
                if next_msg.id == previous_id and self._is_nonrunnable(next_msg):
                    return ResyncOutcome.BLOCKED
                previous_id = next_msg.id
                result = await self._process_backlog_message(
                    next_msg, revision=revision
                )
                if self._stopped:
                    return ResyncOutcome.STOPPED
                match result:
                    case TurnOutcome.NOT_ACCEPTED:
                        return ResyncOutcome.BLOCKED
                    case TurnOutcome.RETRY_LATER:
                        self._set_deferred_head(next_msg.id)
                        return ResyncOutcome.RETRY
                    case TurnOutcome.ADVANCED | TurnOutcome.PAUSED:
                        pass
                if next_msg.id == self._deferred_message_id:
                    self._set_deferred_head(None)
                if self._has_synthetic:
                    return ResyncOutcome.BLOCKED
        except Exception:
            logger.exception(
                "ExecutionContext %s: Error during /next resync", self.room_id
            )
            return ResyncOutcome.RETRY
        return ResyncOutcome.STOPPED

    def _is_nonrunnable(
        self, delivery: PlatformMessage | MessageCreatedPayload
    ) -> bool:
        return self.delivery.eligibility(delivery) not in {
            "runnable",
            "pending",
            "inflight",
        } or self.delivery.is_parked(delivery.id)

    @property
    def _has_synthetic(self) -> bool:
        return any(_is_synthetic(event) for event in self._pending)

    def _oldest_synthetic(self) -> MessageEvent | None:
        """The oldest retained synthetic turn, still sitting in the queue."""
        for event in self._pending:
            if _is_synthetic(event):
                return event
        return None

    def _enqueue_synthetic(self, event: PlatformEvent) -> bool:
        if not _is_synthetic(event):
            return False
        self._retain(event)
        return True

    async def _drain_synthetics(self) -> ResyncOutcome | None:
        """Run retained synthetic turns in receive order.

        The head stays queued until its turn advances, so a pause cannot
        read the same message as progress and spin.
        """
        while self._oldest_synthetic() is not None:
            outcome = await self._finish_synthetic()
            if outcome is not ResyncOutcome.PROGRESS:
                return outcome
        return None

    async def _finish_synthetic(self) -> ResyncOutcome:
        """Advance the oldest synthetic turn; PROGRESS once that one settled."""
        event = self._oldest_synthetic()
        if event is None:
            return ResyncOutcome.PROGRESS
        match await self._process_event(event):
            case TurnOutcome.ADVANCED:
                self._release((_event_message_id(event),))
                return ResyncOutcome.PROGRESS
            case TurnOutcome.PAUSED:
                return ResyncOutcome.STOPPED
            case _:
                return ResyncOutcome.BLOCKED

    def _retained_before(self, next_msg: PlatformMessage | None) -> MessageEvent | None:
        """The oldest retained original that precedes ``next_msg`` (or any, if none).

        An original REST omits (a thread reply) can only be ordered by its own
        key; settled or self-authored ones are dropped here.
        """
        live: list[MessageEvent] = []
        dead: list[str] = []
        for event in self._pending:
            if not _is_ordinary(event):
                continue
            payload = _payload(event)
            if self._is_nonrunnable(payload):
                dead.append(payload.id)
            else:
                live.append(event)
        self._release(dead)
        if not live:
            return None
        oldest = min(live, key=MessageKey.of_event)
        if next_msg is None or MessageKey.of_event(oldest) < MessageKey.of_message(
            next_msg
        ):
            return oldest
        return None

    def _retain_arrival(self, event: PlatformEvent) -> None:
        if isinstance(event, MessageEvent):
            self._retain(event)
        else:
            self._pending.append(event)

    def _absorb_queue(self) -> None:
        """Move queued events onto the retained line, keeping arrival order.

        The live resync request stays queued: this pass began before it, so the
        loop must still serve it with another pass.
        """
        live_resync: ResyncRequest | None = None
        while True:
            try:
                event = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not isinstance(event, ResyncRequest):
                self._retain_arrival(event)
            elif event is self._pending_resync:
                live_resync = event
        if live_resync is not None:
            self.queue.put_nowait(live_resync)  # type: ignore[arg-type]  # The sentinel only wakes this loop.

    async def _service_arrival(self) -> None:
        """React to one arrival: controls only while ordinary work is deferred."""
        if self._stopped:
            return
        if self._ordinary_work_deferred:
            await self._scan_controls()
        if not self._ordinary_work_deferred:
            await self._drain_pending()

    async def _drain_pending(self) -> ResyncOutcome:
        """Run selection passes until the room is empty, blocked or stopped."""
        self._last_selection = ()
        self._scan_cursor = None
        try:
            while (outcome := await self._batch_step()) is ResyncOutcome.PROGRESS:
                # Controls and local boundaries get the loop between passes.
                await asyncio.sleep(0)
        except Exception:
            logger.exception(
                "ExecutionContext %s: Error during batch selection", self.room_id
            )
            return ResyncOutcome.RETRY
        if outcome is ResyncOutcome.EMPTY:
            self._set_deferred_head(None)
            if self._resync_handled_work:
                return ResyncOutcome.WORK
        return outcome

    async def _batch_step(self) -> ResyncOutcome:
        """One selection pass; ``PROGRESS`` means another pass is due."""
        self._absorb_queue()
        if self._stopped:
            return ResyncOutcome.STOPPED
        if self._pending and not _is_ordinary(self._pending[0]):
            return await self._run_boundary()
        revision = self._control_revision
        page = await self.link.get_actionable_page(
            self.room_id, cursor=self._scan_cursor
        )
        self._absorb_queue()
        if self._stopped:
            return ResyncOutcome.STOPPED
        if revision != self._control_revision:
            # A control landed during the read, so the page is stale; unlike a
            # STOP it leaves the room running, which reads again.
            return ResyncOutcome.PROGRESS
        segment, limit = self._local_segment()
        rest = [self._candidate(_backlog_event(msg)) for msg in page.messages]
        retained = [self._candidate(event) for event in segment]
        selection = select_prefix(
            rest,
            retained,
            cap=self.config.max_queued_messages,
            page_reaches=rest[-1].key if rest else None,
            exhausted=page.next_cursor is None,
            limit=limit,
        )
        pruned = self._prune_skipped(retained, rest)
        if not selection.members:
            return self._after_empty_scan(selection, page.next_cursor, pruned)
        return await self._run_selection(selection, revision)

    async def _run_boundary(self) -> ResyncOutcome:
        """Execute the head non-message event or synthetic turn on its own.

        A synthetic stays at the head until it advances. Anything else leaves
        the line once this pass has attempted it.
        """
        event = self._pending[0]
        if _is_synthetic(event):
            return await self._finish_synthetic()
        self._pending.popleft()
        if await self._process_event_body(event) is TurnOutcome.ADVANCED:
            return ResyncOutcome.PROGRESS
        return ResyncOutcome.BLOCKED

    def _local_segment(self) -> tuple[list[MessageEvent], MessageKey | None]:
        """The leading ordinary originals of the line and REST's expansion limit.

        A later non-message event bounds the segment at its last original: a
        REST candidate past that key cannot be placed before or after the event.
        """
        segment: list[MessageEvent] = []
        for event in self._pending:
            if not _is_ordinary(event):
                return segment, MessageKey.of_event(segment[-1]) if segment else None
            segment.append(event)
        return segment, None

    def _candidate(self, event: MessageEvent) -> Candidate:
        disposition = self._disposition(_payload(event))
        if (
            disposition is Disposition.RUNNABLE
            and self._on_message_control is not None
            and self.recognizes_delivery_control(event)
        ):
            disposition = Disposition.CONTROL
        return Candidate(MessageKey.of_event(event), event, disposition)

    def _disposition(self, payload: MessageCreatedPayload) -> Disposition:
        match self.delivery.eligibility(payload):
            case "runnable":
                return Disposition.RUNNABLE
            case "inflight":
                return Disposition.HELD
            case _:
                return Disposition.SKIP

    def _prune_skipped(self, retained: list[Candidate], rest: list[Candidate]) -> bool:
        """Drop retained originals nothing will ever run; True if any went."""
        rest_skipped = {c.message_id for c in rest if c.disposition is Disposition.SKIP}
        gone = [
            c.message_id
            for c in retained
            if c.disposition is Disposition.SKIP or c.message_id in rest_skipped
        ]
        if gone:
            logger.debug(
                "ExecutionContext %s: Releasing retained message(s) %s that no turn will run",
                self.room_id,
                gone,
            )
        self._release(gone)
        return bool(gone)

    def _after_empty_scan(
        self, selection: Selection, next_cursor: str | None, pruned: bool
    ) -> ResyncOutcome:
        if selection.stop is Stop.HELD and selection.at is not None:
            logger.debug(
                "ExecutionContext %s: Message %s is held by another claim; deferring behind it",
                self.room_id,
                selection.at.message_id,
            )
            self._set_deferred_head(selection.at.message_id)
            return ResyncOutcome.BLOCKED
        # Stop.LIMIT is no page verdict: the retained prefix before a boundary was
        # all skipped and has just been pruned, so the first page is read again.
        if next_cursor is not None and selection.stop is not Stop.LIMIT:
            self._scan_cursor = next_cursor
            return ResyncOutcome.PROGRESS
        self._scan_cursor = None
        return ResyncOutcome.PROGRESS if pruned else ResyncOutcome.EMPTY

    async def _run_selection(
        self, selection: Selection, revision: int
    ) -> ResyncOutcome:
        events = selection.events
        ids = _message_ids(events)
        if ids == self._last_selection:
            logger.debug(
                "ExecutionContext %s: Selection %s repeated without progress; "
                "leaving it for the next resync",
                self.room_id,
                ids,
            )
            return ResyncOutcome.BLOCKED
        self._last_selection = ids
        if selection.members[0].disposition is Disposition.CONTROL:
            match await self._probe_control(events[0]):
                case ControlOutcome.CONSUMED:
                    return ResyncOutcome.PROGRESS
                case ControlOutcome.DEFERRED:
                    self._set_deferred_head(ids[0])
                    return ResyncOutcome.RETRY
        outcome = await self.delivery.run(events, revision=revision)
        if self._stopped:
            return ResyncOutcome.STOPPED
        match outcome:
            case TurnOutcome.ADVANCED:
                self._release(ids)
                # An INTERRUPT that consumed the attempt only bumped the
                # revision; the room runs on and what queued behind it is due.
                self._clear_deferred_head_for(events)
                return ResyncOutcome.PROGRESS
            case _ if revision != self._control_revision:
                return ResyncOutcome.BLOCKED
            case TurnOutcome.NOT_ACCEPTED | TurnOutcome.DECLINED:
                return ResyncOutcome.BLOCKED
            case TurnOutcome.RETRY_LATER:
                self._set_deferred_head(ids[0])
                return ResyncOutcome.RETRY
            case TurnOutcome.PAUSED:
                return ResyncOutcome.STOPPED

    async def _process_backlog_message(
        self, msg: PlatformMessage, *, revision: int | None = None
    ) -> TurnOutcome:
        outcome = await self.delivery.run(
            (_backlog_event(msg),),
            self._control_revision if revision is None else revision,
        )
        if outcome is TurnOutcome.ADVANCED:
            self._release((msg.id,))
        return outcome

    def _clear_deferred_head_for(self, events: tuple[MessageEvent, ...]) -> None:
        if self._deferred_message_id in _message_ids(events):
            self._set_deferred_head(None)

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
        ``_execute_message_cycle``, and ``execute_delivery`` stops it after the
        handler drains; participant add/remove events are housekeeping and
        skip it.
        """
        message_id = _delivery_id(event)
        with observe_delivery_phase("cycle", self.room_id, message_id):
            if isinstance(event, MessageEvent):
                await self._execute_message_cycle(event, message_id)
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

    async def _execute_message_cycle(
        self, event: PlatformEvent, message_id: str | None
    ) -> None:
        await self._working_reporter.start()
        scope = self.current_scope
        if scope is not None and scope.terminating:
            raise asyncio.CancelledError
        self._resync_handled_work = True
        self._reset_resync_interval()
        originals = scope.originals if scope is not None else ()
        with observe_delivery_phase("handler", self.room_id, message_id):
            if len(originals) > 1 and self._on_execute_batch is not None:
                await self._on_execute_batch(self, originals)
            else:
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

    async def execute_delivery(
        self,
        scope: TurnScope,
        event: PlatformEvent | None = None,
        *,
        control: bool = False,
    ) -> bool:
        """Supervise one handler, then select its delivery operation once.

        The scope's terminal intent is stored at the synchronous point where the
        handler has finished or been drained, before any later cleanup or
        acknowledgement await.
        """
        if event is None:
            event = scope.originals[0]
        if scope.kind is not None:
            self.delivery.select_intent(scope, None, timed_out=False)
            return await self.abort_delivery(scope.kind, scope)
        if not scope.admitted:
            scope.admit()

        async def invoke() -> None:
            if control:
                assert self._on_message_control is not None
                scope.consumed = await self._on_message_control(self, event)
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
            self.delivery.select_intent(
                scope, owner, timed_out=timed_out, control=control
            )
            if not scope.terminating:
                owner.task.result()
        except asyncio.CancelledError:
            scope.terminate(shutdown=True)
            await self._drain_or_retain(owner)
            raise
        finally:
            wake.cancel()
            await asyncio.gather(wake, return_exceptions=True)
            if isinstance(event, MessageEvent) and not control:
                # stop() joins this cycle inside the settlement budget, which is
                # shorter than the activity POST. A closing execution finishes
                # working:false after that join.
                if self._closing:
                    self._working_reporter.defer_false_report()
                else:
                    await self._working_reporter.stop()
            scope.settle()
        if scope.shutdown:
            raise asyncio.CancelledError
        if scope.kind is not None:
            return await self.abort_delivery(scope.kind, scope)
        if timed_out:
            raise TimeoutError(
                f"cycle exceeded max_cycle_seconds={self.config.max_cycle_seconds}"
            )
        owner.task.result()
        return True

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
            accepted = await self.delivery.processing(message_id)
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

    async def abort_delivery(self, kind: ControlMode, scope: TurnScope) -> bool:
        """Unwind an aborted cycle (interrupt/stop): drop work, send nothing.

        Used by ``execute_delivery`` for a scope terminated by STOP/INTERRUPT after
        its admission. Returns False so the caller sends nothing further.
        ``kind`` is only ever ``INTERRUPT``/``STOP`` here —
        ``interrupt()`` rejects ``PLAY`` before either path can reach this.
        """
        await self._clear_activity()
        # Consume an interrupted message so the idle /next resync does not
        # re-return it and re-fire the cycle the user just interrupted. A
        # failed write stays retained as a pending intent.
        if (
            scope.intent is DeliveryStatus.PROCESSED
            and not await self.delivery.try_settle(scope)
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

    async def _process_event(self, event: PlatformEvent) -> TurnOutcome:
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
            return TurnOutcome.ADVANCED

        # While stopped, leave message triggers actionable for replay on play.
        # Suppression is platform-authoritative; skipping here is a local
        # efficiency short-circuit that avoids claiming/marking (mark->204) and
        # never reaches the adapter (reply->403). A real message stays on the
        # platform for /next; a synthetic one stays at the head of its queue.
        if self._stopped and isinstance(event, MessageEvent):
            logger.debug(
                "ExecutionContext %s: stopped, skipping message %s (left for replay)",
                self.room_id,
                event.payload.id if event.payload else None,
            )
            # The platform replays a listed message on PLAY; only the retained
            # payload can bring back one it omits.
            self._retain(event)
            return TurnOutcome.PAUSED

        payload = event.payload if isinstance(event, MessageEvent) else None
        msg_id = payload.id if payload else None

        if (
            isinstance(event, MessageEvent)
            and msg_id
            and payload
            and not _is_synthetic(event)
        ):
            outcome = await self.delivery.run((event,), self._control_revision)
            if outcome is TurnOutcome.ADVANCED:
                self._release((msg_id,))
            else:
                self._retain(event)
            return outcome

        outcome = await self._process_event_body(event)
        if outcome in DEFERRED_OUTCOMES:
            self._retain(event, first=True)
        return outcome

    def note_turn_failure_reported(self) -> None:
        """Record that the adapter already told the room this turn failed."""
        self._turn_failure_reported = True

    async def report_delivery_failure(self, *, final: bool) -> None:
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
        # Exception details stay in logs; platform writes use the generic message.
        try:
            await AgentTools.from_context(self, bind_scope=False).send_failure(
                AgentFailure(TURN_FAILURE_PROVIDER, GENERIC_PROVIDER_FAILURE_MESSAGE)
            )
        except RoomExecutionStoppedError:
            logger.debug(
                "ExecutionContext %s: failure reporting refused while stopped",
                self.room_id,
            )

    async def _process_event_body(self, event: PlatformEvent) -> TurnOutcome:
        """Run an event that owes no delivery write: housekeeping or a synthetic message."""
        # Participant events are housekeeping, admitted only once their
        # handler starts. A synthetic message admits immediately so a control
        # during its setup is not lost.
        if isinstance(event, MessageEvent):
            scope = await self._begin_turn()
        else:
            scope = await self.begin_delivery()
        if scope is None:
            return TurnOutcome.RETRY_LATER

        logger.debug(
            "Dispatch source=websocket room=%s message=%s event=%s",
            self.room_id,
            _delivery_id(event),
            event.type,
        )

        try:
            # Hydrate context on first event (loads participants always,
            # history only if enable_context_hydration is True)
            await self.prepare_delivery_context()

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
            # execute_delivery and we send nothing further. Only message-driven
            # cycles report the working signal (see _invoke_handler);
            # participant add/remove events are housekeeping and skip it.
            if not await self.execute_delivery(scope, event):
                return self.delivery.aborted_outcome(scope)

            logger.debug("Event %s processed successfully", event.type)
            return TurnOutcome.ADVANCED

        except InputNotAccepted as refusal:
            self.defer_delivery(None, reason=refusal)
            return TurnOutcome.NOT_ACCEPTED

        except RoomExecutionStoppedError:
            return TurnOutcome.RETRY_LATER

        except Exception as e:  # noqa: BLE001 -- non-delivery failures are logged at this boundary
            log_delivery_error(e, "Error processing %s", event.type)
            return TurnOutcome.ADVANCED

        finally:
            self.finish_delivery(scope)


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
