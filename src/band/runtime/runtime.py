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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from band_sdk_core import ClaimRegistry

from band.client.streaming import ControlMode
from band.platform.event import PlatformEvent
from band.runtime.cycle import TurnScope
from band.runtime.execution import Execution, ExecutionContext, ExecutionHandler
from band.runtime.presence import RoomPresence
from band.runtime.types import (
    ParticipantAddedCallback,
    ParticipantRemovedCallback,
    SessionConfig,
)

if TYPE_CHECKING:
    from band.client.streaming import AgentControlPayload
    from band.platform.link import BandLink

logger = logging.getLogger(__name__)


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
class RoomLifecycle:
    """One room's membership intent and the execution it owns.

    ``execution`` stays owned from construction until its cleanup succeeds;
    it receives work only while it is also the room's routed execution.
    ``task`` is the room's single owned startup/retirement operation, and
    ``intent`` counts withdrawals so a startup that outlived one is retired.
    """

    wanted: bool = False
    intent: int = 0
    execution: Execution | None = None
    task: asyncio.Task[bool] | None = None


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
        on_message_control: Callable[[ExecutionContext, PlatformEvent], Awaitable[bool]]
        | None = None,
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
            on_session_cleanup: Optional callback for session cleanup (receives room_id)
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
        self._on_message_control = on_message_control
        self._on_participant_added = on_participant_added
        self._on_participant_removed = on_participant_removed

        # Hub room (set by PlatformRuntime when ContactEventStrategy.HUB_ROOM
        # is active). Forwarded to ExecutionContext so AgentTools can
        # auto-enable contact tools for the hub-room execution path.
        self._hub_room_id: str | None = None

        # RoomPresence for cross-room management
        self.presence = RoomPresence(link, room_filter)

        # Only ``executions`` receives work and control. ``_rooms`` owns every
        # execution, routed or not, until its cleanup succeeds.
        self.executions: dict[str, Execution] = {}
        self._rooms: dict[str, RoomLifecycle] = {}
        self._accepting_rooms = True
        # Accumulates across stop() retries so a cancelled room stays reported.
        self._stop_graceful = True

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

    @property
    def unsettled_rooms(self) -> list[str]:
        """Rooms owning an execution that is not serving them: startup or
        cleanup not yet settled. Rooms still serving are not counted, so an
        empty list does not mean the runtime has stopped."""
        return [
            room_id
            for room_id, record in self._rooms.items()
            if record.execution is not None and not self._serving(room_id)
        ]

    def _serving(self, room_id: str) -> bool:
        record = self._rooms.get(room_id)
        execution = self.executions.get(room_id)
        return (
            record is not None
            and execution is not None
            and execution is record.execution
        )

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
        self._accepting_rooms = True
        self._stop_graceful = True
        try:
            await self.presence.start()
        except BaseException:
            self._accepting_rooms = False
            raise

    async def stop(self, timeout: float | None = None) -> bool:
        """
        Stop the agent runtime with optional graceful timeout.

        Args:
            timeout: Optional seconds to wait for current processing to complete
                     in each execution context. None means cancel immediately.

        Returns:
            True if every execution stopped gracefully, including those
            stopped by an earlier attempt; False if any had to be cancelled.

        Raises:
            ExceptionGroup: Some room cleanup failed after every room was
                attempted. Those rooms stay owned and a later ``stop()``
                retries them.

        1. Stops all execution contexts (with timeout)
        2. Stops RoomPresence
        3. Disconnects the link (wakes ``run(install_signal_handlers=False)``)
        """
        self._accepting_rooms = False
        logger.info("Stopping AgentRuntime for agent %s", self.agent_id)

        failures: list[Exception] = []
        try:
            for room_id in list(self._rooms):
                try:
                    graceful = await self._destroy_execution(room_id, timeout=timeout)
                except Exception as error:  # noqa: BLE001 -- every room must be attempted; failures are raised together below
                    failures.append(error)
                else:
                    self._stop_graceful = self._stop_graceful and graceful

            await self.presence.stop()
        finally:
            # presence.stop leaves the socket up for reuse; a full runtime stop
            # must disconnect so run_forever can return when signals are opted out.
            await self.link.disconnect()
        if failures:
            raise ExceptionGroup("Room cleanup remains incomplete", failures)
        return self._stop_graceful

    async def run(self, *, install_signal_handlers: bool = True) -> None:
        """
        Run the agent until stopped or interrupted.

        Starts the runtime and keeps the WebSocket connection alive.

        ``install_signal_handlers`` is passed to ``BandLink.run_forever``;
        see ``Agent.run_forever`` for when a host turns it off. A host that
        passes ``False`` ends the run by calling ``stop()`` (which disconnects).
        """
        await self.start()
        try:
            await self.link.run_forever(install_signal_handlers=install_signal_handlers)
        except Exception as e:
            logger.error("AgentRuntime error: %s", e)
            raise
        finally:
            await self.stop()

    # --- Presence callbacks ---

    async def _on_room_joined(self, room_id: str, payload: dict) -> None:
        """Record membership and request owned admission without waiting, so
        a slow startup never holds up the presence consumer."""
        if self._accepting_rooms:
            self._request_admission(room_id)

    async def _on_room_left(self, room_id: str) -> None:
        """Fence the room at once and request its owned cleanup.

        Physical teardown is not awaited, so a slow cleanup never holds up
        the presence consumer; a failure stays owned for a later retry.
        """
        record = self._rooms.get(room_id)
        if record is None or not record.wanted:
            return
        self._fence(room_id)
        self._lifecycle(room_id)

    async def _on_room_event(self, room_id: str, event: PlatformEvent) -> None:
        """Handle room event - forward to execution context.

        A room still waiting for old cleanup claims nothing; its admission
        retries instead, and the new execution's /next resync recovers work.
        """
        execution = self.executions.get(room_id)
        record = self._rooms.get(room_id)
        if execution:
            await execution.on_event(event)
        elif record is not None and record.wanted:
            self._lifecycle(room_id)
        else:
            logger.warning("No execution for room %s, event dropped", room_id)

    async def _on_reconnected(self) -> None:
        """Trigger /next resync on all active executions after WebSocket reconnect.

        Messages may have arrived while the socket was down. Each execution
        context re-polls /next to catch anything the server didn't push.
        """
        for room_id in list(self._rooms):
            if not self._serving(room_id):
                self._lifecycle(room_id)
        logger.info(
            "AgentRuntime: Requesting /next resync for %d execution(s) after reconnect",
            len(self.executions),
        )
        for room_id, execution in list(self.executions.items()):
            await self._request_resync(room_id, execution)

    @staticmethod
    async def _request_resync(room_id: str, execution: Execution) -> None:
        if not hasattr(execution, "request_resync"):
            logger.debug(
                "Execution for room %s does not support request_resync, skipping",
                room_id,
            )
            return
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

    async def _create_execution(self, room_id: str) -> Execution | None:
        """Admit a room through its owned lifecycle.

        Waits for a fresh room's startup. Returns None without waiting when an
        earlier execution must be cleaned up first; admission then completes
        through the same owned operation once that cleanup succeeds.
        """
        if not self._accepting_rooms:
            raise RuntimeError("Agent runtime is stopping or stopped")
        if self._serving(room_id):
            logger.debug("Execution already exists for room %s", room_id)
            return self.executions[room_id]
        previous = self._rooms.get(room_id)
        cleanup_first = previous is not None and previous.execution is not None
        lifecycle = self._request_admission(room_id)
        if cleanup_first:
            return None
        await asyncio.shield(lifecycle)
        return self.executions.get(room_id)

    def _request_admission(self, room_id: str) -> asyncio.Task[bool]:
        record = self._rooms.setdefault(room_id, RoomLifecycle())
        record.wanted = True
        return self._lifecycle(room_id)

    def _build_execution(self, room_id: str) -> Execution:
        # Use factory if provided, otherwise create ExecutionContext
        if self._execution_factory:
            try:
                execution = self._execution_factory(
                    room_id,
                    self.link,
                    hub_room_id=self._hub_room_id,
                )
            except TypeError:
                # Backward compatibility: support legacy factories that
                # accept only (room_id, link).
                execution = self._execution_factory(room_id, self.link)
        else:
            execution = ExecutionContext(
                room_id=room_id,
                link=self.link,
                on_execute=self._on_execute,
                on_message_control=self._on_message_control,
                config=self._session_config,
                agent_id=self.agent_id,
                on_participant_added=self._on_participant_added,
                on_participant_removed=self._on_participant_removed,
                hub_room_id=self._hub_room_id,
                claim_registry=self._claim_registry,
                on_platform_stop=self._on_platform_stop,
            )
        return execution

    async def _on_platform_stop(self, ctx: ExecutionContext, scope: TurnScope) -> None:
        if ctx.owns_scope(scope) and self._on_control is not None:
            await self._on_control(ctx.room_id, ControlMode.STOP)

    async def _destroy_execution(
        self, room_id: str, timeout: float | None = None
    ) -> bool:
        """
        Stop and clean up a room's execution, waiting for its owned cleanup.

        A failure raises; the execution stays owned and a later leave,
        rejoin, reconnect or stop retries it.

        Args:
            room_id: Room ID to destroy execution for.
            timeout: Optional seconds to wait for graceful stop.

        Returns:
            True if stopped gracefully, False if cancelled mid-processing.
        """
        record = self._fence(room_id)
        graceful = True
        while record is not None and self._rooms.get(room_id) is record:
            lifecycle = self._lifecycle(room_id, timeout=timeout)
            graceful = await asyncio.shield(lifecycle) and graceful
        return graceful

    def _fence(self, room_id: str) -> RoomLifecycle | None:
        """Withdraw membership: no admission completes and no work routes."""
        self.executions.pop(room_id, None)
        record = self._rooms.get(room_id)
        if record is not None:
            record.wanted = False
            record.intent += 1
        return record

    def _lifecycle(
        self, room_id: str, *, timeout: float | None = None
    ) -> asyncio.Task[bool]:
        """Join the room's owned operation, or start one after the last settled."""
        record = self._rooms[room_id]
        if record.task is None or record.task.done():
            record.task = asyncio.create_task(self._reconcile(room_id, record, timeout))
            record.task.add_done_callback(self._observe_lifecycle)
        return record.task

    @staticmethod
    def _observe_lifecycle(lifecycle: asyncio.Task[bool]) -> None:
        if not lifecycle.cancelled() and (error := lifecycle.exception()):
            logger.warning("Room lifecycle remains pending: %s", error)

    async def _reconcile(
        self, room_id: str, record: RoomLifecycle, timeout: float | None
    ) -> bool:
        """Converge the room's execution to its current membership intent.

        Intent is re-read after every await, so a leave or stop during a held
        startup retires that execution instead of publishing it.
        """
        graceful = True
        while True:
            execution = record.execution
            admit = record.wanted and self._accepting_rooms
            if self._serving(room_id):
                if admit:
                    return graceful
                self.executions.pop(room_id)
            if execution is not None:
                graceful = await self._retire(room_id, record, timeout) and graceful
            elif not admit:
                if self._rooms.get(room_id) is record:
                    del self._rooms[room_id]
                return graceful
            else:
                await self._admit(room_id, record)

    async def _admit(self, room_id: str, record: RoomLifecycle) -> None:
        """Start an owned execution; route it only if the membership intent it
        started under still stands.

        Events arriving before routing are dropped unclaimed, so a routed
        execution always resyncs /next to recover them. Startup has no time
        bound here: leave and stop wait for it to settle, then retire it.
        """
        intent = record.intent
        execution = self._build_execution(room_id)
        record.execution = execution
        await execution.start()
        if not (record.wanted and self._accepting_rooms and record.intent == intent):
            return
        self.executions[room_id] = execution
        logger.debug("Created execution for room %s", room_id)
        await self._request_resync(room_id, execution)

    async def _retire(
        self, room_id: str, record: RoomLifecycle, timeout: float | None
    ) -> bool:
        assert record.execution is not None
        graceful = await record.execution.stop(timeout=timeout)

        # Durable completion state is safe to release with the room. Pending
        # acknowledgements remain in the shared registry so a later rejoin
        # retries only the ack instead of replaying handler side effects.
        self._claim_registry.discard_completed(room_id)

        # Adapter room cleanup (checkpointer, native sessions, etc.)
        if self._on_session_cleanup:
            await self._on_session_cleanup(room_id)

        record.execution = None
        logger.debug("Destroyed execution for room %s", room_id)
        return graceful
