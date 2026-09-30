"""
AgentRuntime - Convenience wrapper combining RoomPresence + Execution management.

For SDK-heavy users who want managed execution contexts.
Framework-light users can use RoomPresence or BandLink directly.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Protocol

from band_sdk_core import ClaimRegistry
from tenacity import AsyncRetrying, retry_if_result, wait_fixed

from band.client.streaming import ControlMode
from band.platform.event import PlatformEvent

from .execution import Execution, ExecutionContext, ExecutionHandler
from .presence import RoomPresence
from .teardown import RoomTeardown
from .types import (
    ParticipantAddedCallback,
    ParticipantRemovedCallback,
    SessionConfig,
)

if TYPE_CHECKING:
    from band.client.streaming import AgentControlPayload
    from band.platform.link import BandLink

logger = logging.getLogger(__name__)

# Wait between attempts to bring a room's execution up, whether a previous
# execution's stop is still failing or the new one failed to build or start.
CREATION_RETRY_WAIT_S = 1.0


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
class RoomCreation:
    """The owner bringing an admitted room's execution up.

    ``first_attempt`` resolves with the execution (or None) after the first
    try, so a join returns promptly while ``task`` keeps retrying.
    """

    first_attempt: asyncio.Future[Execution | None]
    task: asyncio.Task[None] | None = None

    def settle(self, execution: Execution | None) -> None:
        """Release the joiner with the first attempt's outcome, once."""
        if not self.first_attempt.done():
            self.first_attempt.set_result(execution)


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
        on_control: Callable[[str, ControlMode], Awaitable[None]] | None = None,
        on_participant_added: ParticipantAddedCallback | None = None,
        on_participant_removed: ParticipantRemovedCallback | None = None,
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
            on_control: Optional callback for an interrupt/stop control signal
                (receives room_id and the ``ControlMode``), invoked in addition
                to the execution's own ``interrupt()``/``stop_room()`` -- for
                adapter work that keeps running after those return early
            on_participant_added: Optional callback for participant_added events
            on_participant_removed: Optional callback for participant_removed events
        """
        self.link = link
        self.agent_id = agent_id
        self._on_execute = on_execute
        self._execution_factory = execution_factory
        self._session_config = session_config or SessionConfig()
        self._on_session_cleanup = on_session_cleanup
        self._on_control = on_control
        self._on_participant_added = on_participant_added
        self._on_participant_removed = on_participant_removed

        # Hub room (set by PlatformRuntime when ContactEventStrategy.HUB_ROOM
        # is active). Forwarded to ExecutionContext so AgentTools can
        # auto-enable contact tools for the hub-room execution path.
        self._hub_room_id: str | None = None

        # RoomPresence for cross-room management
        self.presence = RoomPresence(link, room_filter)

        # Per-room executions
        self.executions: dict[str, Execution] = {}
        # A room's execution that is not (or not yet) live and whose stop and
        # cleanup have not both succeeded. Destroy callers share its one
        # in-flight attempt, a failed one leaves it for a retry, and room
        # creation waits on it, so old cleanup never meets a rejoined room.
        self._teardowns: dict[str, RoomTeardown] = {}
        # One owner per admitted room bringing its execution up: it finishes a
        # predecessor's teardown, builds and starts a candidate, and retries
        # after any failure until an execution is live or the room is left.
        self._pending_creations: dict[str, RoomCreation] = {}

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

        INTERRUPT/STOP also notify ``on_control`` (the adapter's own
        ``on_interrupt``), unconditionally -- the execution's own method only
        reaches the task that's still running the handler; it cannot cancel
        work an adapter has already returned from and kept running detached
        (e.g. a turn parked on a human decision).
        """
        match mode:
            case ControlMode.INTERRUPT | ControlMode.STOP:
                attr = "interrupt" if mode == ControlMode.INTERRUPT else "stop_room"
                fn = getattr(execution, attr, None)
                if fn is None:
                    logger.debug(
                        "Execution for room %s has no %s(); skipping",
                        getattr(execution, "room_id", "?"),
                        attr,
                    )
                else:
                    fn()
                room_id = getattr(execution, "room_id", None)
                if self._on_control is not None and room_id is not None:
                    await self._on_control(room_id, mode)
            case ControlMode.PLAY:
                fn = getattr(execution, "resume_room", None)
                if fn is None:
                    logger.debug(
                        "Execution for room %s has no resume_room(); skipping",
                        getattr(execution, "room_id", "?"),
                    )
                    return
                await fn()
            case _:
                logger.warning("Unknown control mode %r; ignoring", mode)

    # --- Execution management ---
    #
    # An execution is owned from the moment it exists until its stop and room
    # cleanup both succeed (RoomTeardown), so no failure or cancel can leak it.
    #   join  (_create_execution): returns after the first attempt; a failed
    #         one keeps retrying in the background until live or the room is left.
    #   leave (_destroy_execution): returns after one attempt; a failed one stays
    #         owned and is retried by the next leave or join.
    # A start() or stop() that outlasts SessionConfig.start_stop_deadline_seconds
    # is abandoned and counts as a failed attempt.

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
        retrying = AsyncRetrying(
            retry=retry_if_result(lambda execution: execution is None),
            wait=wait_fixed(CREATION_RETRY_WAIT_S),
            before_sleep=lambda _state: creation.settle(None),
        )
        execution = None
        try:
            execution = await retrying(self._attempt_creation, room_id)
        finally:
            creation.settle(execution)
            if self._pending_creations.get(room_id) is creation:
                del self._pending_creations[room_id]

    async def _attempt_creation(self, room_id: str) -> Execution | None:
        """One atomic try: finish any predecessor teardown, then build, start
        and only then publish a candidate. None when the room is not live yet."""
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
            with self._owning(room_id, execution):
                async with asyncio.timeout(
                    self._session_config.start_stop_deadline_seconds
                ):
                    await execution.start()
                self._raise_swallowed_cancel()
        except Exception:
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
            True if stopped gracefully, False if cancelled mid-processing or
            if the stop or cleanup failed (still owned, retried by the next call).
        """
        creation = self._pending_creations.pop(room_id, None)
        if creation is not None and creation.task is not None:
            # Awaited, so a candidate the cancelled attempt was starting is
            # already owned as a teardown below.
            creation.task.cancel()
            _, unwinding = await asyncio.wait(
                {creation.task},
                timeout=self._session_config.start_stop_deadline_seconds,
            )
            if unwinding:
                creation.settle(None)
                logger.warning(
                    "Starting the execution for %s ignored cancellation; "
                    "the next leave retries",
                    room_id,
                )
                return False
        teardown = self._teardowns.get(room_id)
        if teardown is None:
            execution = self.executions.pop(room_id, None)
            if execution is None:
                return True
            teardown = self._own(room_id, execution)
        return await self._run_teardown(room_id, teardown, timeout)

    async def _run_teardown(
        self, room_id: str, teardown: RoomTeardown, timeout: float | None
    ) -> bool:
        """Run one teardown attempt; a failure is logged and reported as False."""
        try:
            return await teardown.run(timeout)
        except Exception:
            logger.warning("Tearing down room %s failed", room_id, exc_info=True)
            return False

    @staticmethod
    def _raise_swallowed_cancel() -> None:
        """Honor a cancel that start() swallowed, so it cannot go live after a leave."""
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError

    @contextmanager
    def _owning(self, room_id: str, execution: Execution) -> Iterator[None]:
        """Own the execution while the block runs.

        Ownership ends only if the block completes; an error or a cancel leaves
        the teardown registered, so a failed start cannot leak or look live.
        """
        self._own(room_id, execution)
        yield
        self._forget_teardown(room_id)

    def _own(self, room_id: str, execution: Execution) -> RoomTeardown:
        """Register the execution's teardown; it stays until stop and cleanup succeed."""
        teardown = RoomTeardown(
            execution,
            cleanup=partial(self._cleanup_room, room_id),
            forget=partial(self._forget_teardown, room_id),
            deadline_s=self._session_config.start_stop_deadline_seconds,
        )
        self._teardowns[room_id] = teardown
        return teardown

    def _forget_teardown(self, room_id: str) -> None:
        del self._teardowns[room_id]

    async def _cleanup_room(self, room_id: str) -> None:
        # Durable completion state is safe to release with the room. Pending
        # acknowledgements remain in the shared registry so a later rejoin
        # retries only the ack instead of replaying handler side effects.
        self._claim_registry.discard_completed(room_id)
        if self._on_session_cleanup:
            await self._on_session_cleanup(room_id)
        logger.debug("Destroyed execution for room %s", room_id)
