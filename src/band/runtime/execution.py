"""Room scheduling, context and ownership of asynchronous turn effects."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from typing import (
    TYPE_CHECKING,
    Any,
    Protocol,
    runtime_checkable,
)

from band_rest.types.agent_activity_detail import AgentActivityDetail
from band_sdk_core import (
    AgentFailure,
    ClaimRegistry,
    ParticipantRoster,
    RetryTracker,
)

from band.client.rest import DEFAULT_REQUEST_OPTIONS
from band.client.streaming import ControlMode
from band.core.activity import ToolNamesObserver
from band.core.exceptions import (
    CleanupIncompleteError,
    InputNotAccepted,
    RoomExecutionStoppedError,
)
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TURN_FAILURE_PROVIDER,
)
from band.core.retention import task_failure
from band.core.settlement import SettlementResult
from band.core.turn_lifecycle import (
    TurnTask,
    finished_within_grace,
    terminal_deadline,
)
from band.core.types import (
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
from band.runtime.activity import ActivityObserver
from band.runtime.callbacks import ParticipantAddedCallback, ParticipantRemovedCallback
from band.runtime.context import ConversationContext
from band.runtime.context_serialization import context_item_to_dict
from band.runtime.cycle import TurnScope
from band.runtime.delivery import (
    DeliveryLifecycle,
    TurnOutcome,
    log_delivery_error,
    observe_delivery_phase,
)
from band.runtime.formatters import build_participants_message, format_history_for_llm
from band.runtime.originals import delivery_id
from band.runtime.participants import log_roster_call, log_roster_error
from band.runtime.scheduler import ResyncRequest, Scheduler
from band.runtime.sessionconfig import SessionConfig
from band.runtime.tools.agent import AgentTools
from band.runtime.working_state import WorkingStateReporter

if TYPE_CHECKING:
    from band.platform.link import BandLink

logger = logging.getLogger(__name__)

# Messages whose failed control recognition already logged its traceback.
MAX_RECOGNIZER_FAILURES_LOGGED = 256


class ExecutionState(StrEnum):
    """Lifecycle state for one room execution."""

    STARTING = "starting"
    IDLE = "idle"
    PROCESSING = "processing"


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

        Called preemptively, outside the room's message queue, on an
        ``interrupt`` or ``stop`` control signal. ``AgentRuntime``
        ``hasattr``-guards this, so custom ``Execution`` implementations that
        omit it degrade to a no-op.
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


@dataclass(frozen=True)
class ScopeActivityObserver:
    context: ExecutionContext
    scope: TurnScope

    async def update(self, activity: AgentActivityDetail) -> None:
        await self.context.observe_activity(self.scope, activity=activity)

    async def set_manual_wait(self, waiting: bool) -> None:
        await self.context.observe_activity(self.scope, waiting=waiting)


class ExecutionContext:
    """Own room context and provider tasks under serialized delivery scheduling.

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
            is_message_control: Read-only recognition of a delivery the
                adapter would handle as a control. Without it controls are
                handled in ordinary queue order.
        """
        self.config = config or SessionConfig()
        self.room_id = room_id
        self.link = link
        self._on_execute = on_execute
        self._on_execute_batch = on_execute_batch
        self._on_message_control = on_message_control
        self._is_message_control = is_message_control
        self._recognizer_failures: OrderedDict[str | None, None] = OrderedDict()
        self._agent_id = agent_id
        self._on_participant_added = on_participant_added
        self._on_participant_removed = on_participant_removed
        self.hub_room_id = hub_room_id

        # Per-room working-state reporter. Disabled for the contact-hub
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
        self.queue: asyncio.Queue[PlatformEvent | ResyncRequest] = asyncio.Queue()
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
        self._retry_tracker = RetryTracker(max_retries=self.config.max_message_retries)
        self.delivery = DeliveryLifecycle(
            self.room_id,
            self.link,
            self.claims,
            self._retry_tracker,
            self.config,
            self,
            self._agent_id,
        )
        # Graceful shutdown: event signaled when state becomes idle
        self._idle_event: asyncio.Event = asyncio.Event()
        self._idle_event.set()  # Start as idle

        # Pending system messages to inject (e.g., contact broadcasts)
        self._pending_system_messages: list[str] = []
        self._detached_cycle_tasks: dict[asyncio.Task[None], TurnTask] = {}
        self.current_scope: TurnScope | None = None
        self._activity_owner: TurnScope | None = None
        self._activity_stop_scope: TurnScope | None = None
        self._activity_completion: asyncio.Task[None] | None = None
        # Tool names follow the adapter's tool-narration choice; the default
        # matches emit's opt-out semantics, so only adapters that drop
        # Emit.TOOL_CALLS (via sync_activity_tool_names) hide them.
        self._activity_tool_names = True
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

        self.scheduler = Scheduler(self)

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
        return any(
            scope is not None and scope.observer_task is not None
            for scope in (self.current_scope, self._activity_stop_scope)
        )

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
        self.scheduler.unpark()
        self._process_loop_task = asyncio.create_task(
            self.scheduler.run(),
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
        # Only work retained before this attempt may receive another cancellation.
        retry_owners = tuple(self._detached_cycle_tasks.values())
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
        self._activity_owner = None
        await self._join_activity_completion()
        await self._stop_retained_tasks(retry_owners)
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
        Reconnect events trigger a fresh /next synchronization immediately.
        """
        if isinstance(event, ReconnectedEvent):
            logger.info(
                "ExecutionContext %s: Reconnected, scheduling synchronization",
                self.room_id,
            )
            self.scheduler.unpark()
            await self.request_resync()
            return

        self.queue.put_nowait(event)
        logger.debug("Event %s enqueued for room %s", event.type, self.room_id)

    async def request_resync(self) -> None:
        """Queue an immediate /next reconciliation, coalescing pending requests."""
        self.scheduler.request_resync()

    def interrupt(self, *, kind: ControlMode | str = ControlMode.INTERRUPT) -> bool:
        """Abort the in-flight reasoning cycle, if any.

        Called on a control signal, outside the cycle. It only terminates the
        running turn scope; all message-status / dedupe bookkeeping happens once in
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
        activity = self._activity_owner
        if activity is not None and activity is not scope:
            activity.terminate(kind)
            self._finish_activity(activity, aborted=True)
        if scope is not None:
            scope.terminate(kind)
        return scope is not None or activity is not None

    def stop_room(self) -> None:
        """Pause triggers and abort the cycle, leaving it actionable for PLAY."""
        self._stopped = True
        self.scheduler.reset_interval()
        self.interrupt(kind=ControlMode.STOP)

    async def resume_room(self) -> None:
        """Clear the local pause and request immediate reconciliation."""
        self.resume_from_gate()
        await self.request_resync()

    def resume_from_gate(self) -> None:
        self._control_revision += 1
        self._stopped = False
        self.scheduler.reset_interval()

    def owns_stop_observation(self, scope: TurnScope) -> bool:
        return (
            scope
            in (self.current_scope, self._activity_owner, self._activity_stop_scope)
            and scope.stop_revision == self._control_revision
        )

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
        if scope is self._activity_owner:
            self._activity_stop_scope = scope
            if scope.settled:
                self._finish_activity(scope, aborted=True)
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

    async def _begin_turn(self) -> TurnScope | None:
        """Begin a message turn that controls terminate from this point on."""
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

    async def _stop_retained_tasks(self, retry_owners: tuple[TurnTask, ...]) -> None:
        owners = tuple(self._detached_cycle_tasks.values())
        deadline = terminal_deadline()
        for owner in retry_owners:
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

    def defer_delivery(
        self, message_id: str | None, *, reason: InputNotAccepted
    ) -> None:
        if message_id:
            self.scheduler.defer(message_id)
        logger.debug(
            "ExecutionContext %s: Input %s not accepted (%s); "
            "keeping it pending for scheduler recovery",
            self.room_id,
            message_id or "synthetic",
            reason,
        )

    @property
    def handles_message_control(self) -> bool:
        return self._on_message_control is not None

    def recognizes_delivery_control(self, event: MessageEvent) -> bool:
        """Whether the host handles ``event`` as a control; a failing recognizer leaves it ordinary."""
        if self._is_message_control is None:
            return False
        try:
            return self._is_message_control(self, event)
        except Exception:
            identity = delivery_id(event)
            # Recognition runs for every candidate on every pass.
            first = self._first_recognizer_failure(identity)
            logger.log(
                logging.ERROR if first else logging.DEBUG,
                "Room %s: control recognition failed for message %s; handling it as ordinary input",
                self.room_id,
                identity,
                exc_info=first,
            )
            return False

    def _first_recognizer_failure(self, identity: str | None) -> bool:
        if identity in self._recognizer_failures:
            return False
        self._recognizer_failures[identity] = None
        if len(self._recognizer_failures) > MAX_RECOGNIZER_FAILURES_LOGGED:
            self._recognizer_failures.popitem(last=False)
        return True

    async def _invoke_handler(self, event: PlatformEvent) -> None:
        """Coroutine wrapper around the execution handler so it can run as a
        cancellable ``asyncio.Task`` (the handler is typed ``Awaitable``).

        Message-driven cycles start the working-state signal in
        ``_execute_message_cycle``, and ``execute_delivery`` stops it after the
        handler drains; participant add/remove events are housekeeping and
        skip it.
        """
        message_id = delivery_id(event)
        with observe_delivery_phase("cycle", self.room_id, message_id):
            if isinstance(event, MessageEvent):
                await self._execute_message_cycle(event, message_id)
            else:
                await self._on_execute(self, event)

    async def _report_working_state(self, working: bool) -> bool:
        """Report working state and visible activity (wired into the reporter)."""
        return await self.link.report_activity(
            self.room_id,
            working,
            activity=self._visible_activity,
            timeout_seconds=self.config.working_request_timeout_seconds,
            on_stopped=partial(
                self._observe_activity_stop,
                self._activity_owner or self.current_scope,
                self._control_revision,
            ),
        )

    def _observe_activity_stop(self, scope: TurnScope | None, revision: int) -> None:
        if (
            scope is None
            or not (
                scope in (self._activity_owner, self.current_scope)
                and revision == self._control_revision
            )
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
        scope = self.current_scope
        if scope is not None:
            scope.reports_activity = True
            await self._acquire_activity(scope)
        if scope is not None and scope.terminating:
            raise asyncio.CancelledError
        self.scheduler.handled_work = True
        self.scheduler.reset_interval()
        originals = scope.originals if scope is not None else ()
        with observe_delivery_phase("handler", self.room_id, message_id):
            if len(originals) > 1 and self._on_execute_batch is not None:
                await self._on_execute_batch(self, originals)
            else:
                await self._on_execute(self, event)

    async def _join_activity_completion(self) -> None:
        task = self._activity_completion
        if task is not None:
            await asyncio.shield(task)
            if self._activity_completion is task:
                self._activity_completion = None

    async def _acquire_activity(self, scope: TurnScope) -> None:
        await self._join_activity_completion()
        await self._start_activity(scope)

    async def _start_activity(self, scope: TurnScope) -> None:
        previous = self._activity_owner
        if previous is not None and previous is not scope:
            if not previous.aborted and any(
                not worker.task.done() for worker in previous.workers
            ):
                return
            self._finish_activity(previous, aborted=True)
            await self._join_activity_completion()
        if scope.aborted or self._closing:
            return
        if self._activity_owner is not scope:
            self._activity_stop_scope = None
        self._activity_owner = scope
        await self._working_reporter.start()

    def set_activity_tool_names(self, enabled: bool) -> None:
        """Gate tool names in room activity to match tool-call narration.

        Permission waits and memory activity are unaffected: they name no
        content. The gate is consulted at report time, so an observer bound
        before this change (a continuation's adapter pumping tool events while
        the runner re-reads the choice) still follows the live choice.
        """
        self._activity_tool_names = enabled

    def activity_observer(self, scope: TurnScope | None) -> ActivityObserver | None:
        if scope is None:
            return None
        return ToolNamesObserver(
            ScopeActivityObserver(self, scope),
            hidden=lambda: not self._activity_tool_names,
        )

    @property
    def _visible_activity(self) -> AgentActivityDetail | None:
        """The owner's detail, or none for an aborted owner so its stale work is
        never sent (the platform shows a report without detail as reasoning)."""
        owner = self._activity_owner
        if owner is None or owner.aborted:
            return None
        return owner.activity.detail

    async def observe_activity(
        self,
        scope: TurnScope,
        *,
        activity: AgentActivityDetail | None = None,
        waiting: bool | None = None,
    ) -> None:
        """Record a turn's activity; never starts reporting or waits on it."""
        before = self._visible_activity
        scope.activity.apply(activity=activity, waiting=waiting)
        if self._visible_activity != before:
            self._working_reporter.refresh()

    def register_turn_task(self, scope: TurnScope, owner: TurnTask) -> None:
        """Bind completion to the same owner that delivers worker cancellation."""
        scope.bind(owner)
        owner.task.add_done_callback(lambda _: self._finish_activity(scope))
        if (
            scope.reports_activity
            and scope is self.current_scope
            and self._activity_owner is None
        ):
            previous = self._activity_completion

            async def acquire() -> None:
                if previous is not None:
                    await asyncio.shield(previous)
                if scope is self.current_scope:
                    await self._start_activity(scope)

            self._retain_activity_completion(asyncio.create_task(acquire()))

    def _retain_activity_completion(self, task: asyncio.Task[None]) -> None:
        self._activity_completion = task
        task.add_done_callback(self._activity_completed)

    def _activity_completed(self, task: asyncio.Task[None]) -> None:
        task_failure(task)
        if self._activity_completion is task:
            self._activity_completion = None
            if self._activity_owner is not None:
                self._finish_activity(self._activity_owner)

    def _finish_activity(self, scope: TurnScope, *, aborted: bool = False) -> None:
        if self._activity_owner is not scope:
            return
        if not aborted and (
            not scope.settled or any(not owner.task.done() for owner in scope.workers)
        ):
            return
        self._activity_owner = None
        self._working_reporter.defer_false_report()
        if self._activity_completion is None:
            self._retain_activity_completion(
                asyncio.create_task(self._working_reporter.stop())
            )

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
            scope.settle()
            if isinstance(event, MessageEvent) and not control:
                self._finish_activity(scope, aborted=scope.terminating)
                if not self._closing:
                    await self._join_activity_completion()
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
            result = await self.delivery.processing(message_id)
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
        return result is SettlementResult.ACCEPTED

    async def abort_delivery(self, kind: ControlMode, scope: TurnScope) -> bool:
        """Unwind an aborted cycle (interrupt/stop) and report it as aborted.

        Used by ``execute_delivery`` for a scope terminated by STOP/INTERRUPT after
        its admission. The intent already stored decides the terminal write,
        which the delivery lifecycle sends like any other turn's.
        ``kind`` is only ever ``INTERRUPT``/``STOP`` here —
        ``interrupt()`` rejects ``PLAY`` before either path can reach this.
        """
        await self._clear_activity()
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

    @property
    def delivery_context_token(self) -> object:
        return self._context_cache

    def finish_startup(self) -> None:
        self._set_state(ExecutionState.IDLE)

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

    async def execute_local(self, event: PlatformEvent) -> TurnOutcome:
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
        outcome = await self._run_local(scope, event)
        # A begun roster event already applied its update and callback, so
        # however it ended it is never replayed.
        return outcome if isinstance(event, MessageEvent) else TurnOutcome.ADVANCED

    async def _run_local(self, scope: TurnScope, event: PlatformEvent) -> TurnOutcome:
        logger.debug(
            "Dispatch source=websocket room=%s message=%s event=%s",
            self.room_id,
            delivery_id(event),
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

            # Call execution handler as a cancellable cycle; a control signal
            # can abort just this turn. Only message-driven cycles report the
            # working signal (see _invoke_handler); participant add/remove
            # events are housekeeping and skip it.
            if await self.execute_delivery(scope, event):
                logger.debug("Event %s processed successfully", event.type)
            return self.delivery.settled_outcome(scope)

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
