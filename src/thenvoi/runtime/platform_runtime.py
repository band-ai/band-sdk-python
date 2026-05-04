"""Platform runtime - framework-agnostic connectivity."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from thenvoi.client.rest import DEFAULT_REQUEST_OPTIONS
from thenvoi.platform.link import ThenvoiLink
from thenvoi.platform.event import ContactEvent, MessageEvent, PlatformEvent
from thenvoi.runtime.contact_handler import ContactEventHandler
from thenvoi.runtime.runtime import AgentRuntime
from thenvoi.runtime.execution import ExecutionContext
from thenvoi.runtime.types import (
    AgentConfig,
    ContactEventConfig,
    ContactEventStrategy,
    ParticipantAddedCallback,
    ParticipantRemovedCallback,
    SessionConfig,
)
from thenvoi_rest.core.api_error import ApiError

logger = logging.getLogger(__name__)


class PlatformRuntime:
    """
    Manages platform connectivity.

    Handles:
    - ThenvoiLink (WebSocket + REST)
    - AgentRuntime (room presence, execution)
    - Agent metadata fetching

    Does NOT handle:
    - Message preprocessing
    - History conversion
    - LLM framework logic
    """

    _METADATA_RETRY_DELAYS_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)

    def __init__(
        self,
        agent_id: str,
        api_key: str,
        ws_url: str = "wss://app.thenvoi.com/api/v1/socket/websocket",
        rest_url: str = "https://app.thenvoi.com",
        config: AgentConfig | None = None,
        session_config: SessionConfig | None = None,
        contact_config: ContactEventConfig | None = None,
        on_participant_added: ParticipantAddedCallback | None = None,
        on_participant_removed: ParticipantRemovedCallback | None = None,
    ):
        self._agent_id = agent_id
        self._api_key = api_key
        self._ws_url = ws_url
        self._rest_url = rest_url
        self._config = config or AgentConfig()
        self._session_config = session_config or SessionConfig()
        self._contact_config = contact_config or ContactEventConfig()
        self._on_participant_added = on_participant_added
        self._on_participant_removed = on_participant_removed

        self._link: ThenvoiLink | None = None
        self._runtime: AgentRuntime | None = None
        self._agent_name: str = ""
        self._agent_description: str = ""
        self._contact_handler: ContactEventHandler | None = None
        self._pending_broadcasts: list[str] = []
        self._contacts_subscribed: bool = False
        self._pending_hub_room_id: str | None = (
            None  # Hub room waiting for ExecutionContext
        )

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def agent_name(self) -> str:
        return self._agent_name

    @property
    def agent_description(self) -> str:
        return self._agent_description

    @property
    def link(self) -> ThenvoiLink:
        if not self._link:
            raise RuntimeError("Runtime not started")
        return self._link

    @property
    def runtime(self) -> AgentRuntime:
        if not self._runtime:
            raise RuntimeError("Runtime not started")
        return self._runtime

    @property
    def contact_config(self) -> ContactEventConfig:
        """Get the contact event configuration."""
        return self._contact_config

    @property
    def is_contacts_subscribed(self) -> bool:
        """Check if subscribed to contact events channel."""
        return self._contacts_subscribed

    async def initialize(self) -> None:
        """
        Initialize link and fetch agent metadata without starting message processing.

        Call this before start() to access agent name/description before
        the runtime begins processing messages. This enables adapters to
        initialize their system prompts before any messages arrive.

        This method is idempotent - safe to call multiple times.

        Note: This only creates the REST client and fetches metadata.
        WebSocket connection happens later in start() when RoomPresence starts.
        """
        if self._link:
            return  # Already initialized

        self._link = ThenvoiLink(
            agent_id=self._agent_id,
            api_key=self._api_key,
            ws_url=self._ws_url,
            rest_url=self._rest_url,
        )

        await self._fetch_agent_metadata()
        logger.debug("Platform runtime initialized for agent: %s", self._agent_name)

    async def start(
        self,
        on_execute: Callable[[ExecutionContext, PlatformEvent], Awaitable[None]],
        on_cleanup: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """
        Start platform runtime (begin processing messages).

        Call initialize() first if you need to access agent metadata
        before starting. Otherwise, this method will initialize automatically.

        Args:
            on_execute: Callback for message execution
            on_cleanup: Callback for session cleanup
        """
        # Auto-initialize if not already done
        await self.initialize()

        # Type narrowing: initialize() guarantees _link is set
        assert self._link is not None

        self._runtime = AgentRuntime(
            link=self._link,
            agent_id=self._agent_id,
            on_execute=on_execute,
            session_config=self._session_config,
            on_session_cleanup=on_cleanup or self._noop_cleanup,
            on_participant_added=self._on_participant_added,
            on_participant_removed=self._on_participant_removed,
        )

        await self._runtime.start()

        # Set up contact event handling after WebSocket is connected
        await self._setup_contact_handling()

        logger.info("Platform runtime started for agent: %s", self._agent_name)

    async def stop(self, timeout: float | None = None) -> bool:
        """
        Stop platform runtime with optional graceful timeout.

        Args:
            timeout: Optional seconds to wait for current processing to complete.
                     None means cancel immediately.

        Returns:
            True if stopped gracefully, False if cancelled mid-processing.
        """
        graceful = True
        if self._runtime:
            graceful = await self._runtime.stop(timeout=timeout)

        # Unsubscribe from contacts channel before disconnecting
        if self._link and self._contacts_subscribed:
            await self._link.unsubscribe_agent_contacts()
            self._contacts_subscribed = False
            logger.debug("Unsubscribed from contacts channel")

        if self._link:
            await self._link.disconnect()
        logger.info("Platform runtime stopped")
        return graceful

    async def run_forever(self) -> None:
        """Run until interrupted."""
        if self._link:
            await self._link.run_forever()

    async def _fetch_agent_metadata(self) -> None:
        """Fetch agent metadata from platform."""
        if not self._link:
            raise RuntimeError("Link not initialized")

        attempts = len(self._METADATA_RETRY_DELAYS_SECONDS) + 1
        for attempt, delay in enumerate(
            (0.0, *self._METADATA_RETRY_DELAYS_SECONDS), start=1
        ):
            if delay > 0:
                logger.warning(
                    "Rate limited fetching metadata for agent %s; retrying in %.1fs "
                    "(attempt %s/%s)",
                    self._agent_id,
                    delay,
                    attempt,
                    attempts,
                )
                await asyncio.sleep(delay)

            try:
                response = await self._link.rest.agent_api_identity.get_agent_me(
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
                break
            except ApiError as exc:
                if exc.status_code != 429 or attempt == attempts:
                    raise
        else:
            raise RuntimeError("Metadata fetch retry loop exited unexpectedly")

        if not response.data:
            raise RuntimeError("Failed to fetch agent metadata")

        agent = response.data
        if not agent.description:
            raise ValueError(f"Agent {self._agent_id} has no description")

        self._agent_name = agent.name
        self._agent_description = agent.description
        logger.debug("Fetched metadata for agent: %s", self._agent_name)

    @staticmethod
    async def _noop_cleanup(room_id: str) -> None:
        pass

    async def _setup_contact_handling(self) -> None:
        """Set up contact event handling based on config."""
        if self._contact_config.strategy == ContactEventStrategy.DISABLED:
            if not self._contact_config.broadcast_changes:
                logger.debug("Contact handling disabled")
                return
            # Even if DISABLED, we may want broadcasts

        assert self._link is not None
        assert self._runtime is not None

        # Create handler with broadcast callback if enabled
        broadcast_fn = None
        if self._contact_config.broadcast_changes:
            broadcast_fn = self._queue_broadcast

        # Create hub event callbacks for HUB_ROOM strategy
        hub_event_fn = None
        hub_init_fn = None
        if self._contact_config.strategy == ContactEventStrategy.HUB_ROOM:
            hub_event_fn = self._inject_hub_event
            hub_init_fn = self._inject_hub_system_prompt

        self._contact_handler = ContactEventHandler(
            config=self._contact_config,
            link=self._link,
            on_broadcast=broadcast_fn,
            on_hub_event=hub_event_fn,
            on_hub_init=hub_init_fn,
        )

        # Set up contact event callback on presence
        self._runtime.presence.on_contact_event = self._on_contact_event

        # Subscribe to contacts channel
        await self._link.subscribe_agent_contacts(self._agent_id)
        self._contacts_subscribed = True

        # For HUB_ROOM strategy, create hub room at startup
        if self._contact_config.strategy == ContactEventStrategy.HUB_ROOM:
            hub_room_id = await self._contact_handler.initialize_hub_room()
            # The room_added WebSocket event will arrive and create ExecutionContext
            # We'll mark the hub room as ready when that happens
            self._pending_hub_room_id = hub_room_id
            # Forward to AgentRuntime so the hub-room ExecutionContext gets it
            # at construction time, which lets AgentTools auto-enable contact
            # tools for that room.
            self._runtime.set_hub_room_id(hub_room_id)
            logger.info("Hub room initialized at startup: %s", hub_room_id)

        logger.info(
            "Contact handling enabled: strategy=%s, broadcast=%s",
            self._contact_config.strategy.value,
            self._contact_config.broadcast_changes,
        )

    async def _on_contact_event(self, event: ContactEvent) -> None:
        """Handle contact event from WebSocket."""
        logger.debug("PlatformRuntime received contact event: %s", type(event).__name__)
        if self._contact_handler:
            await self._contact_handler.handle(event)
        else:
            logger.warning("Contact event received but no handler configured")

            # Process any pending broadcasts
            await self._process_broadcasts()

    def _queue_broadcast(self, message: str) -> None:
        """Queue a broadcast message for injection into all sessions."""
        self._pending_broadcasts.append(message)

    async def _process_broadcasts(self) -> None:
        """Inject pending broadcasts into all active sessions."""
        if not self._pending_broadcasts or not self._runtime:
            return

        messages = self._pending_broadcasts.copy()
        self._pending_broadcasts.clear()

        # Inject into all active execution contexts
        for room_id, execution in self._runtime.active_sessions.items():
            for msg in messages:
                try:
                    execution.inject_system_message(f"[Contacts]: {msg}")
                    logger.debug("Broadcast injected into room %s: %s", room_id, msg)
                except Exception as e:
                    logger.warning(
                        "Failed to inject broadcast into room %s: %s", room_id, e
                    )

    async def _inject_hub_event(self, hub_room_id: str, event: MessageEvent) -> None:
        """
        Inject a MessageEvent into the hub room's ExecutionContext.

        Args:
            hub_room_id: The hub room ID
            event: The MessageEvent to inject
        """
        if not self._runtime:
            raise RuntimeError("Runtime not started")

        # Get ExecutionContext (should exist since hub room created at startup)
        execution = self._runtime.executions.get(hub_room_id)
        if not execution:
            raise RuntimeError(f"ExecutionContext not found for hub room {hub_room_id}")

        # Mark hub room as ready if this is the first successful injection
        if self._contact_handler and self._pending_hub_room_id == hub_room_id:
            self._contact_handler.mark_hub_room_ready()
            self._pending_hub_room_id = None

        # Inject the event into the execution queue
        await execution.on_event(event)
        logger.debug("Event injected into hub room %s", hub_room_id)

    async def _inject_hub_system_prompt(self, hub_room_id: str, prompt: str) -> None:
        """
        Inject a system prompt into the hub room's ExecutionContext.

        Called once when the hub room is first used to set up the agent's
        instructions for managing contact requests.

        Args:
            hub_room_id: The hub room ID
            prompt: The system prompt to inject
        """
        if not self._runtime:
            raise RuntimeError("Runtime not started")

        # Get ExecutionContext (should exist since hub room created at startup)
        execution = self._runtime.executions.get(hub_room_id)
        if not execution:
            raise RuntimeError(f"ExecutionContext not found for hub room {hub_room_id}")

        # Inject the system prompt
        execution.inject_system_message(prompt)
        logger.info("System prompt injected into hub room %s", hub_room_id)
