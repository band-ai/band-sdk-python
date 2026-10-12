"""
AgentRuntime - Convenience wrapper combining RoomPresence + Execution management.

For SDK-heavy users who want managed execution contexts.
Framework-light users can use RoomPresence or BandLink directly.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol

from band_sdk_core import ClaimRegistry

from band.client.streaming import ControlMode
from band.platform.event import PlatformEvent
from band.runtime.control import ControlDeduplicator, apply_control, control_targets
from band.runtime.cycle import TurnScope
from band.runtime.execution import Execution, ExecutionContext, ExecutionHandler
from band.runtime.presence import RoomPresence
from band.runtime.rooms import RoomExecutions, request_resync
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

        self.rooms = RoomExecutions(
            build=self._build_execution, release=self._release_room
        )
        # Accumulates across stop() retries so a cancelled room stays reported.
        self._stop_graceful = True
        self._control_ids = ControlDeduplicator()

        # Shared by default contexts so a room/message pair executes at most
        # once per runtime, including across context recreation.
        self._claim_registry = ClaimRegistry()

        # Set up presence callbacks
        self.presence.on_room_joined = self._on_room_joined
        self.presence.on_room_left = self._on_room_left
        self.presence.on_room_event = self._on_room_event
        self.presence.on_reconnected = self._on_reconnected

    @property
    def executions(self) -> dict[str, Execution]:
        """Routed executions by room_id: the only ones receiving work."""
        return self.rooms.routed

    @property
    def active_sessions(self) -> dict[str, Execution]:
        """Get active execution contexts by room_id."""
        return self.executions.copy()

    @property
    def unsettled_rooms(self) -> list[str]:
        """Rooms owning an execution that is not serving them."""
        return self.rooms.unsettled

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
        self.rooms.accepting = True
        self._stop_graceful = True
        try:
            await self.presence.start()
        except BaseException:
            self.rooms.accepting = False
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
        self.rooms.close(timeout=timeout)
        logger.info("Stopping AgentRuntime for agent %s", self.agent_id)

        failures: list[Exception] = []
        try:
            for room_id in self.rooms.room_ids():
                try:
                    graceful = await self.rooms.destroy(room_id, timeout=timeout)
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
            raise ExceptionGroup("Execution cleanup remains incomplete", failures)
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
        if self.rooms.accepting:
            self.rooms.request_admission(room_id)

    async def _on_room_left(self, room_id: str) -> None:
        """Fence the room at once and request its owned cleanup.

        Physical teardown is not awaited, so a slow cleanup never holds up
        the presence consumer; a failure stays owned for a later retry.
        """
        self.rooms.withdraw(room_id)

    async def _on_room_event(self, room_id: str, event: PlatformEvent) -> None:
        """Forward a room event to its routed execution.

        A wanted room that is not routed yet holds the event for the execution
        its admission starts, and the hold retries that admission.
        """
        if (execution := self.executions.get(room_id)) is not None:
            await execution.on_event(event)
        else:
            self.rooms.hold(room_id, event)

    async def _on_reconnected(self) -> None:
        """Trigger /next resync on all active executions after WebSocket reconnect.

        Messages may have arrived while the socket was down. Each execution
        context re-polls /next to catch anything the server didn't push.
        """
        self.rooms.converge_unserved()
        logger.info(
            "AgentRuntime: Requesting /next resync for %d execution(s) after reconnect",
            len(self.executions),
        )
        for room_id, execution in list(self.executions.items()):
            await request_resync(room_id, execution)

    # --- Control signals ---

    async def handle_control(self, payload: AgentControlPayload) -> None:
        """Apply an ``agent.control`` signal (interrupt/stop/play) to executions.

        Invoked directly from the WebSocket receive task (via
        ``BandLink.on_control``) so it can preempt a cycle already in flight.

        Routing:
        - ``scope == "agent"`` with no ``room_id`` → fan out to all executions.
        - any signal with a ``room_id`` → that room only.
        - unknown room → no-op.

        Dedupes on ``correlation_id`` (the server does not). A signal that
        reached no room is not recorded, so a later copy still applies once
        the room is routed. Note: exceptions raised here are
        swallowed-and-logged by the channel handler, so this method must not
        rely on propagating errors to signal anything.
        """
        targets = control_targets(payload, self.executions)
        if not targets or not self._control_ids.first_sighting(payload):
            return
        logger.info(
            "Applying control mode=%s scope=%s to %d room(s) "
            "(correlation_id=%s execution_id=%s)",
            payload.mode,
            payload.scope,
            len(targets),
            payload.correlation_id,
            payload.execution_id,
        )
        for execution in targets:
            await apply_control(execution, payload.mode, self._on_control)

    # --- Execution management ---

    def _build_execution(self, room_id: str) -> Execution:
        if self._execution_factory is None:
            return self._default_execution(room_id)
        try:
            return self._execution_factory(
                room_id, self.link, hub_room_id=self._hub_room_id
            )
        except TypeError:
            # Legacy factories accept only (room_id, link).
            return self._execution_factory(room_id, self.link)

    def _default_execution(self, room_id: str) -> ExecutionContext:
        return ExecutionContext(
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

    async def _on_platform_stop(self, ctx: ExecutionContext, scope: TurnScope) -> None:
        if ctx.owns_scope(scope) and self._on_control is not None:
            await self._on_control(ctx.room_id, ControlMode.STOP)

    async def _release_room(self, room_id: str) -> None:
        """Room-scoped cleanup once its execution has stopped."""
        # Durable completion state is safe to release with the room. Pending
        # acknowledgements remain in the shared registry so a later rejoin
        # retries only the ack instead of replaying handler side effects.
        self._claim_registry.discard_completed(room_id)
        # Adapter room cleanup (checkpointer, native sessions, etc.)
        if self._on_session_cleanup:
            await self._on_session_cleanup(room_id)
