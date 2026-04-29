"""
AgentRuntime - Convenience wrapper combining RoomPresence + Execution management.

For SDK-heavy users who want managed execution contexts.
Framework-light users can use RoomPresence or ThenvoiLink directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable, Protocol

from thenvoi.platform.event import PlatformEvent

from .execution import Execution, ExecutionContext, ExecutionHandler
from .presence import RoomPresence
from .types import (
    ParticipantAddedCallback,
    ParticipantRemovedCallback,
    SessionConfig,
)

if TYPE_CHECKING:
    from thenvoi.platform.link import ThenvoiLink

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
        link: "ThenvoiLink",
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
        link = ThenvoiLink(agent_id, api_key, ...)

        async def on_execute(ctx: ExecutionContext, event: PlatformEvent):
            if isinstance(event, MessageEvent):
                tools = AgentTools.from_context(ctx)
                # Process message with LLM...

        runtime = AgentRuntime(link, agent_id, on_execute=on_execute)
        await runtime.run()

    Example (custom execution factory):
        def letta_factory(
            room_id: str,
            link: ThenvoiLink,
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
        link: "ThenvoiLink",
        agent_id: str,
        on_execute: ExecutionHandler,
        execution_factory: ExecutionFactory | None = None,
        room_filter: Callable[[dict], bool] | None = None,
        session_config: SessionConfig | None = None,
        on_session_cleanup: Callable[[str], Awaitable[None]] | None = None,
        on_participant_added: ParticipantAddedCallback | None = None,
        on_participant_removed: ParticipantRemovedCallback | None = None,
    ):
        """
        Initialize AgentRuntime.

        Args:
            link: ThenvoiLink for WebSocket and REST
            agent_id: Agent ID from Thenvoi platform
            on_execute: Callback for handling execution events
            execution_factory: Optional factory for custom Execution implementations
            room_filter: Optional filter to decide which rooms to join
            session_config: Configuration for ExecutionContext
            on_session_cleanup: Optional callback for session cleanup (receives room_id)
            on_participant_added: Optional callback for participant_added events
            on_participant_removed: Optional callback for participant_removed events
        """
        self.link = link
        self.agent_id = agent_id
        self._on_execute = on_execute
        self._execution_factory = execution_factory
        self._session_config = session_config or SessionConfig()
        self._on_session_cleanup = on_session_cleanup
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
        for room_id in list(self.executions.keys()):
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
            except Exception as e:
                logger.warning("Failed to request resync for room %s: %s", room_id, e)

    # --- Execution management ---

    async def _create_execution(self, room_id: str) -> Execution:
        """Create and start execution context for a room."""
        if room_id in self.executions:
            logger.debug("Execution already exists for room %s", room_id)
            return self.executions[room_id]

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
                config=self._session_config,
                agent_id=self.agent_id,
                on_participant_added=self._on_participant_added,
                on_participant_removed=self._on_participant_removed,
                hub_room_id=self._hub_room_id,
            )

        self.executions[room_id] = execution
        await execution.start()

        logger.debug("Created execution for room %s", room_id)
        return execution

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
        if room_id not in self.executions:
            return True

        execution = self.executions.pop(room_id)
        graceful = await execution.stop(timeout=timeout)

        # Call cleanup callback (for adapter to clean up checkpointer, etc.)
        if self._on_session_cleanup:
            try:
                await self._on_session_cleanup(room_id)
            except Exception as e:
                logger.warning("Session cleanup callback failed for %s: %s", room_id, e)

        logger.debug("Destroyed execution for room %s", room_id)
        return graceful
