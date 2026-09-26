"""
AgentRuntime - Convenience wrapper combining RoomPresence + Execution management.

For SDK-heavy users who want managed execution contexts.
Framework-light users can use RoomPresence or BandLink directly.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from band_sdk_core import ClaimRegistry

from band.client.streaming import ControlMode
from band.platform.event import PlatformEvent

from .execution import Execution, ExecutionContext, ExecutionHandler
from .presence import RoomPresence
from .types import (
    ParticipantAddedCallback,
    ParticipantRemovedCallback,
    SessionConfig,
)

if TYPE_CHECKING:
    from band.client.streaming import AgentControlPayload
    from band.platform.link import BandLink

logger = logging.getLogger(__name__)

# Delay between retries of a previous execution's failed stop before a
# rejoined room's new execution is created.
TEARDOWN_RETRY_DELAY_S = 1.0


class ExecutionFactory(Protocol):
    """Factory type for custom execution implementations.

    Preferred signature supports hub-room propagation:
        factory(room_id, link, hub_room_id=<id or None>)

    Legacy two-argument factories are still supported for backward compatibility.
    """

    def __call__(
        self,
        room_id: str,
        link: BandLink,
        *,
        hub_room_id: str | None = None,
    ) -> Execution: ...


@dataclass
class RoomTeardown:
    """A room's execution awaiting a successful stop, plus the current attempt.

    ``immediate`` is set once any caller asks for an immediate stop; it cuts a
    graceful attempt's wait short and is never cleared, so a later graceful
    request cannot extend an immediate one.
    """

    execution: Execution
    attempt: asyncio.Task[bool] | None = None
    immediate: asyncio.Event = field(default_factory=asyncio.Event)
    # The stop's graceful result once it has returned; a retry after a failed
    # cleanup reuses it instead of stopping the execution again.
    stopped: bool | None = None


@dataclass
class RoomCreation:
    """The owner bringing an admitted room's execution up.

    ``first_attempt`` resolves with the execution (or None) after the first
    try, so a join returns promptly while ``task`` keeps retrying.
    """

    first_attempt: asyncio.Future[Execution | None]
    task: asyncio.Task[None] | None = None


class AgentRuntime:
    """
    Convenience wrapper: RoomPresence + Execution management.

    For SDK-heavy users who want managed execution contexts.
    Framework-light users can use RoomPresence directly.

    Manages:
    - Agent presence across rooms via RoomPresence
    - Per-room execution contexts via ExecutionContext (or custom)
    - Lifecycle coordination (start, stop, run)

    Example (default execution):
        link = BandLink(agent_id, api_key, ...)

        async def on_execute(ctx: ExecutionContext, event: PlatformEvent):
            if isinstance(event, MessageEvent):
                tools = AgentTools.from_context(ctx)
                # Process message with LLM...

        runtime = AgentRuntime(link, agent_id, on_execute=on_execute)
        await runtime.run()

    Example (custom execution factory):
        def letta_factory(
            room_id: str,
            link: BandLink,
            *,
            hub_room_id: str | None = None,
        ) -> Execution:
            return LettaExecution(room_id, link, hub_room_id=hub_room_id)

        runtime = AgentRuntime(
            link,
            agent_id,
            on_execute=my_handler,
            execution_factory=letta_factory,
        )
        await runtime.run()
    """

    def __init__(
        self,
        link: BandLink,
        agent_id: str,
        on_execute: ExecutionHandler,
        execution_factory: ExecutionFactory | None = None,
        room_filter: Callable[[dict], bool] | None = None,
        session_config: SessionConfig | None = None,
        on_session_cleanup: Callable[[str], Awaitable[None]] | None = None,
        on_participant_added: ParticipantAddedCallback | None = None,
        on_participant_removed: ParticipantRemovedCallback | None = None,
        on_idle_release: Callable[[str], Awaitable[None]] | None = None,
    ):
        """
        Initialize AgentRuntime.

        Args:
            link: BandLink for WebSocket and REST
            agent_id: Agent ID from Band platform
            on_execute: Callback for handling execution events
            execution_factory: Optional factory for custom Execution implementations
            room_filter: Optional filter to decide which rooms to join
            session_config: Configuration for ExecutionContext
            on_session_cleanup: Optional callback for session cleanup (receives
                room_id). If it raises, the room's teardown stays owned and the
                callback is called again on the next teardown attempt, so it
                must be safe to retry after a partial failure.
            on_participant_added: Optional callback for participant_added events
            on_idle_release: Optional callback (receives room_id) run when a
                room has been idle for ``SessionConfig.release_idle_room_after_s``
            on_participant_removed: Optional callback for participant_removed events
        """
        self.link = link
        self.agent_id = agent_id
        self._on_execute = on_execute
        self._execution_factory = execution_factory
        self._session_config = session_config or SessionConfig()
        self._on_session_cleanup = on_session_cleanup
        self._on_participant_added = on_participant_added
        self._on_idle_release = on_idle_release
        self._on_participant_removed = on_participant_removed

        # Hub room (set by PlatformRuntime when ContactEventStrategy.HUB_ROOM
        # is active). Forwarded to ExecutionContext so AgentTools can
        # auto-enable contact tools for the hub-room execution path.
        self._hub_room_id: str | None = None

        # RoomPresence for cross-room management
        self.presence = RoomPresence(link, room_filter)

        # Per-room executions
        self.executions: dict[str, Execution] = {}
        # A room whose execution was taken out of ``executions`` but whose
        # stop and cleanup have not both succeeded. Destroy callers share its
        # one in-flight attempt, a failed stop leaves it for a retry, and room
        # creation waits on it, so old cleanup never meets a rejoined room.
        self._teardowns: dict[str, RoomTeardown] = {}
        # One owner per admitted room bringing its execution up: it finishes a
        # predecessor's teardown, builds and starts a candidate, and retries
        # after any failure until an execution is live or the room is left.
        self._pending_creations: dict[str, RoomCreation] = {}
        self._teardown_retry_delay_s = TEARDOWN_RETRY_DELAY_S

        # Control-signal dedup. The server does not deduplicate
        # agent.control pushes, so we drop repeats by correlation_id. Bounded
        # LRU; only touched from the WebSocket receive task.
        self._seen_control_ids: OrderedDict[str, bool] = OrderedDict()
        self._max_seen_control_ids: int = 256

        # Shared by default contexts so a room/message pair executes at most
        # once per runtime, including across context recreation.
        self._claim_registry = ClaimRegistry()

        # Set up presence callbacks
        self.presence.on_room_joined = self._on_room_joined
        self.presence.on_room_left = self._on_room_left
        self.presence.on_room_event = self._on_room_event
        self.presence.on_reconnected = self._on_reconnected

    @property
    def active_sessions(self) -> dict[str, Execution]:
        """Get active execution contexts by room_id."""
        return self.executions.copy()

    def set_hub_room_id(self, hub_room_id: str | None) -> None:
        """Register the hub-room ID so future executions can auto-enable contact tools.

        Called by PlatformRuntime when ContactEventStrategy.HUB_ROOM is active
        and the hub room has been created. AgentTools constructed for the hub
        room (room_id == hub_room_id) will force-include contact-management
        tool schemas regardless of adapter feature settings.
        """
        self._hub_room_id = hub_room_id

    async def start(self) -> None:
        """
        Start the agent runtime.

        1. Starts RoomPresence (connects link, subscribes to rooms)
        2. Creates execution contexts for existing rooms
        """
        logger.info("Starting AgentRuntime for agent %s", self.agent_id)
        await self.presence.start()

    async def stop(self, timeout: float | None = None) -> bool:
        """
        Stop the agent runtime with optional graceful timeout.

        Args:
            timeout: Optional seconds to wait for current processing to complete
                     in each execution context. None means cancel immediately.

        Returns:
            True if all executions stopped gracefully, False if any had to be
            cancelled mid-processing.

        1. Stops all execution contexts (with timeout)
        2. Stops RoomPresence
        """
        logger.info("Stopping AgentRuntime for agent %s", self.agent_id)

        # Stop all executions with timeout
        all_graceful = True
        for room_id in list(
            {**self._pending_creations, **self._teardowns, **self.executions}
        ):
            graceful = await self._destroy_execution(room_id, timeout=timeout)
            all_graceful = all_graceful and graceful

        await self.presence.stop()
        return all_graceful

    async def run(self) -> None:
        """
        Run the agent until stopped or interrupted.

        Starts the runtime and keeps the WebSocket connection alive.
        """
        await self.start()
        try:
            await self.link.run_forever()
        except Exception as e:
            logger.error("AgentRuntime error: %s", e)
            raise
        finally:
            await self.stop()

    # --- Presence callbacks ---

    async def _on_room_joined(self, room_id: str, payload: dict) -> None:
        """Handle room joined - create execution context."""
        await self._create_execution(room_id)

    async def _on_room_left(self, room_id: str) -> None:
        """Handle room left - destroy execution context."""
        await self._destroy_execution(room_id)

    async def _on_room_event(self, room_id: str, event: PlatformEvent) -> None:
        """Handle room event - forward to execution context."""
        execution = self.executions.get(room_id)
        if execution:
            await execution.on_event(event)
        else:
            logger.warning("No execution for room %s, event dropped", room_id)

    async def _on_reconnected(self) -> None:
        """Trigger /next resync on all active executions after WebSocket reconnect.

        Messages may have arrived while the socket was down. Each execution
        context re-polls /next to catch anything the server didn't push.
        """
        logger.info(
            "AgentRuntime: Requesting /next resync for %d execution(s) after reconnect",
            len(self.executions),
        )
        for room_id, execution in list(self.executions.items()):
            if not hasattr(execution, "request_resync"):
                logger.debug(
                    "Execution for room %s does not support request_resync, skipping",
                    room_id,
                )
                continue
            try:
                await execution.request_resync()
            except Exception as e:  # noqa: BLE001 -- runtime loop must log and continue rather than crash the agent process
                logger.warning("Failed to request resync for room %s: %s", room_id, e)

    # --- Control signals ---

    async def handle_control(self, payload: AgentControlPayload) -> None:
        """Apply an ``agent.control`` signal (interrupt/stop/play) to executions.

        Invoked directly from the WebSocket receive task (via
        ``BandLink.on_control``) so it can preempt a cycle already in flight.

        Routing:
        - ``scope == "agent"`` with no ``room_id`` → fan out to all executions.
        - any signal with a ``room_id`` → that room only.
        - unknown room → no-op.

        Dedupes on ``correlation_id`` (the server does not). Note: exceptions
        raised here are swallowed-and-logged by the channel handler, so this
        method must not rely on propagating errors to signal anything.
        """
        cid = payload.correlation_id
        if cid is not None:
            if cid in self._seen_control_ids:
                logger.debug("Duplicate control signal %s ignored", cid)
                return
            self._seen_control_ids[cid] = True
            self._seen_control_ids.move_to_end(cid)
            if len(self._seen_control_ids) > self._max_seen_control_ids:
                self._seen_control_ids.popitem(last=False)
        else:
            logger.debug(
                "Control signal mode=%s has no correlation_id; not deduped",
                payload.mode,
            )

        # Resolve target executions.
        if payload.room_id is not None:
            execution = self.executions.get(payload.room_id)
            if execution is None:
                logger.info(
                    "Control signal (mode=%s) for unknown room %s; no-op",
                    payload.mode,
                    payload.room_id,
                )
                return
            targets = [execution]
        elif payload.scope == "agent":
            targets = list(self.executions.values())
        else:
            logger.warning(
                "Control signal scope=%s with no room_id; no-op", payload.scope
            )
            return

        logger.info(
            "Applying control mode=%s scope=%s to %d room(s) "
            "(correlation_id=%s execution_id=%s)",
            payload.mode,
            payload.scope,
            len(targets),
            cid,
            payload.execution_id,
        )

        for execution in targets:
            await self._apply_control(execution, payload.mode)

    async def _apply_control(self, execution: Execution, mode: ControlMode) -> None:
        """Dispatch one control mode to one execution, degrading gracefully.

        Custom ``Execution`` implementations that omit the control methods are
        skipped with a log (mirrors how ``request_resync`` degrades).
        """
        if mode == ControlMode.INTERRUPT:
            fn = getattr(execution, "interrupt", None)
            if fn is None:
                logger.debug(
                    "Execution for room %s has no interrupt(); skipping",
                    getattr(execution, "room_id", "?"),
                )
                return
            fn()
        elif mode == ControlMode.STOP:
            fn = getattr(execution, "stop_room", None)
            if fn is None:
                logger.debug(
                    "Execution for room %s has no stop_room(); skipping",
                    getattr(execution, "room_id", "?"),
                )
                return
            fn()
        elif mode == ControlMode.PLAY:
            fn = getattr(execution, "resume_room", None)
            if fn is None:
                logger.debug(
                    "Execution for room %s has no resume_room(); skipping",
                    getattr(execution, "room_id", "?"),
                )
                return
            await fn()
        else:
            logger.warning("Unknown control mode %r; ignoring", mode)

    # --- Execution management ---

    async def _create_execution(self, room_id: str) -> Execution | None:
        """Bring up the room's execution through its single creation owner.

        Returns the live execution, or None when the first attempt failed; the
        owner then keeps retrying in the background until one is live.
        """
        if room_id in self.executions:
            logger.debug("Execution already exists for room %s", room_id)
            return self.executions[room_id]
        creation = self._pending_creations.get(room_id)
        if creation is None:
            creation = RoomCreation(asyncio.get_running_loop().create_future())
            self._pending_creations[room_id] = creation
            creation.task = asyncio.ensure_future(self._bring_up(room_id, creation))
        return await asyncio.shield(creation.first_attempt)

    async def _bring_up(self, room_id: str, creation: RoomCreation) -> None:
        """Retry creation until an execution is live; cancelled by a leave."""
        try:
            execution = await self._attempt_creation(room_id)
            creation.first_attempt.set_result(execution)
            while execution is None:
                await asyncio.sleep(self._teardown_retry_delay_s)
                execution = await self._attempt_creation(room_id)
        finally:
            if not creation.first_attempt.done():
                creation.first_attempt.set_result(None)
            if self._pending_creations.get(room_id) is creation:
                del self._pending_creations[room_id]

    async def _attempt_creation(self, room_id: str) -> Execution | None:
        """One atomic try: finish any predecessor teardown, then build, start
        and only then publish a candidate. None when the room is not live yet.

        A candidate whose start fails (or is cancelled) is handed to a
        teardown before anything else happens, so it cannot leak or be seen
        as live.
        """
        teardown = self._teardowns.get(room_id)
        if teardown is not None:
            await self._run_teardown(room_id, teardown, timeout=None)
            if self._teardowns.get(room_id) is teardown:
                return None
        try:
            execution = self._build_execution(room_id)
        except Exception:
            logger.warning(
                "Building the execution for %s failed", room_id, exc_info=True
            )
            return None
        try:
            await execution.start()
        except BaseException as exc:
            self._teardowns[room_id] = RoomTeardown(execution)
            if not isinstance(exc, Exception):
                raise
            logger.warning(
                "Starting the execution for %s failed", room_id, exc_info=True
            )
            return None
        self.executions[room_id] = execution
        logger.debug("Created execution for room %s", room_id)
        return execution

    def _build_execution(self, room_id: str) -> Execution:
        # Use factory if provided, otherwise create ExecutionContext
        if self._execution_factory:
            try:
                return self._execution_factory(
                    room_id,
                    self.link,
                    hub_room_id=self._hub_room_id,
                )
            except TypeError:
                # Backward compatibility: support legacy factories that
                # accept only (room_id, link).
                return self._execution_factory(room_id, self.link)
        return ExecutionContext(
            room_id=room_id,
            link=self.link,
            on_execute=self._on_execute,
            config=self._session_config,
            agent_id=self.agent_id,
            on_participant_added=self._on_participant_added,
            on_participant_removed=self._on_participant_removed,
            hub_room_id=self._hub_room_id,
            claim_registry=self._claim_registry,
            on_idle_release=self._on_idle_release,
        )

    async def _destroy_execution(
        self, room_id: str, timeout: float | None = None
    ) -> bool:
        """
        Stop and cleanup execution context for a room.

        Args:
            room_id: Room ID to destroy execution for.
            timeout: Optional seconds to wait for graceful stop.

        Returns:
            True if stopped gracefully, False if cancelled mid-processing.
        """
        creation = self._pending_creations.pop(room_id, None)
        if creation is not None and creation.task is not None:
            # Awaited, so a candidate the cancelled attempt was starting is
            # already recorded as a teardown below.
            creation.task.cancel()
            await asyncio.wait({creation.task})
        teardown = self._teardowns.get(room_id)
        if teardown is None:
            execution = self.executions.pop(room_id, None)
            if execution is None:
                return True
            teardown = RoomTeardown(execution)
            self._teardowns[room_id] = teardown
        return await self._run_teardown(room_id, teardown, timeout)

    async def _run_teardown(
        self, room_id: str, teardown: RoomTeardown, timeout: float | None
    ) -> bool:
        """Join the room's in-flight teardown attempt, starting one if needed.

        Shielded: a cancelled caller leaves the attempt running for the next
        caller. An attempt whose stop or cleanup raised is logged and retried
        by the next caller; the execution stays owned until both succeed.
        """
        if timeout is None:
            teardown.immediate.set()
        attempt = teardown.attempt
        if attempt is None or (attempt.done() and attempt.exception() is not None):
            attempt = asyncio.ensure_future(self._tear_down(room_id, teardown, timeout))
            teardown.attempt = attempt
        try:
            return await asyncio.shield(attempt)
        except Exception:
            logger.warning("Tearing down room %s failed", room_id, exc_info=True)
            return False

    @staticmethod
    async def _stop_execution(teardown: RoomTeardown, timeout: float | None) -> bool:
        """Stop the execution, letting an immediate request preempt a graceful one.

        Only one ``stop()`` call runs at a time. A graceful stop that an
        immediate request interrupts is cancelled first and, if it had not
        finished, followed by ``stop(timeout=None)``; either way the outcome is
        non-graceful for every waiter.
        """
        execution = teardown.execution
        if timeout is None or teardown.immediate.is_set():
            return await execution.stop(timeout=None)
        graceful_stop = asyncio.ensure_future(execution.stop(timeout=timeout))
        preempted = asyncio.ensure_future(teardown.immediate.wait())
        try:
            await asyncio.wait(
                {graceful_stop, preempted}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            preempted.cancel()
        if graceful_stop.done():
            return graceful_stop.result()
        graceful_stop.cancel()
        await asyncio.wait({graceful_stop})
        if graceful_stop.cancelled():
            await execution.stop(timeout=None)
        else:
            graceful_stop.result()
        return False

    async def _tear_down(
        self, room_id: str, teardown: RoomTeardown, timeout: float | None
    ) -> bool:
        """Stop the execution, then run room cleanup and forget the teardown.

        A raising stop or cleanup propagates, keeping the teardown (and its
        execution) owned for a retry; a retry after a failed cleanup runs only
        the cleanup again.
        """
        if teardown.stopped is None:
            teardown.stopped = await self._stop_execution(teardown, timeout)

        # Durable completion state is safe to release with the room. Pending
        # acknowledgements remain in the shared registry so a later rejoin
        # retries only the ack instead of replaying handler side effects.
        self._claim_registry.discard_completed(room_id)
        if self._on_session_cleanup:
            await self._on_session_cleanup(room_id)

        if self._teardowns.get(room_id) is teardown:
            del self._teardowns[room_id]
        logger.debug("Destroyed execution for room %s", room_id)
        return teardown.stopped
