"""A2A Gateway Adapter that exposes Thenvoi peers as A2A endpoints."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from typing import ClassVar
from uuid import uuid4

from a2a.types import (
    Message as A2AMessage,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from a2a.utils import get_message_text

from thenvoi.client.rest import (
    AsyncRestClient,
    ChatEventRequest,
    ChatMessageRequest,
    ChatMessageRequestMentionsItem,
    ChatRoomRequest,
    DEFAULT_REQUEST_OPTIONS,
    ParticipantRequest,
)
from thenvoi.converters.a2a_gateway import GatewayHistoryConverter
from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AdapterFeatures, Capability, Emit, PlatformMessage
from thenvoi.integrations.a2a.gateway.server import GatewayServer
from thenvoi.integrations.a2a.gateway.types import GatewaySessionState, PendingA2ATask
from thenvoi_rest import Peer
from thenvoi_rest.agent_api_peers.types.list_agent_peers_response import (
    ListAgentPeersResponse,
)
from thenvoi_rest.core.api_error import ApiError

logger = logging.getLogger(__name__)


def slugify(name: str) -> str:
    """Convert name to URL-safe slug.

    Args:
        name: The name to slugify.

    Returns:
        URL-safe slug (lowercase, alphanumeric with dashes).
    """
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)  # Replace non-alphanumeric with -
    return slug.strip("-")  # Remove leading/trailing dashes


class A2AGatewayAdapter(SimpleAdapter[GatewaySessionState]):
    """Gateway adapter exposing Thenvoi peers as A2A endpoints.

    This adapter enables remote A2A agents to interact with Thenvoi platform
    peers through standard A2A HTTP endpoints. It acts as a bridge:
    - Receives A2A messages via HTTP server
    - Creates/reuses Thenvoi chat rooms for context management
    - Sends messages to peers via REST API
    - Streams responses back via SSE

    Uses direct REST client (not AgentToolsProtocol) because:
    - AgentToolsProtocol is room-bound (passed in on_message with room context)
    - Gateway receives HTTP requests outside of on_message() context
    - Gateway needs to send messages to SPECIFIC rooms

    Example:
        from thenvoi import Agent
        from thenvoi.integrations.a2a.gateway import A2AGatewayAdapter

        adapter = A2AGatewayAdapter(
            rest_url="https://app.thenvoi.com",
            api_key="your-api-key",
            gateway_url="http://localhost:10000",
            port=10000,
        )
        agent = Agent.create(
            adapter=adapter,
            agent_id="sap-gateway",
            api_key="your-api-key",
        )
        await agent.run()
    """

    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset()
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset()

    def __init__(
        self,
        rest_url: str = "https://app.thenvoi.com",
        api_key: str = "",
        gateway_url: str = "http://localhost:10000",
        port: int = 10000,
        features: AdapterFeatures | None = None,
    ) -> None:
        """Initialize gateway adapter.

        Args:
            rest_url: Base URL for Thenvoi REST API.
            api_key: API key for authentication (same as Agent.create()).
            gateway_url: Base URL for A2A endpoints exposed by this gateway.
            port: Port for HTTP server to listen on.
        """
        super().__init__(
            history_converter=GatewayHistoryConverter(),
            features=features,
        )
        self.gateway_url = gateway_url
        self.port = port

        # Direct REST client for room/message operations
        self._rest = AsyncRestClient(base_url=rest_url, api_key=api_key)

        # Peers keyed by slug (primary) and UUID (fallback)
        self._peers: dict[str, Peer] = {}  # slug → Peer
        self._peers_by_uuid: dict[str, Peer] = {}  # uuid → Peer
        self._server: GatewayServer | None = None

        # Session state (rehydrated from history)
        self._context_to_room: dict[str, str] = {}
        self._room_participants: dict[str, set[str]] = {}

        # Request/response correlation
        self._pending_tasks: dict[str, PendingA2ATask] = {}  # room_id → task
        self._peer_discovery_retry_delays_seconds: tuple[float, ...] = (
            1.0,
            2.0,
            4.0,
            8.0,
            16.0,
        )

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Fetch peers via REST and start HTTP server.

        Args:
            agent_name: Name of this agent.
            agent_description: Description of this agent.
        """
        await super().on_started(agent_name, agent_description)

        # Fetch ALL peers at startup using REST client (with pagination)
        all_peers = await self._fetch_all_peers_with_retry()

        # Build slug and UUID mappings
        for peer in all_peers:
            slug = slugify(peer.name)
            self._peers[slug] = peer
            self._peers_by_uuid[peer.id] = peer

        logger.info("Discovered %d peers for gateway", len(self._peers))

        # Create and start HTTP server with peer routes
        self._server = GatewayServer(
            peers=self._peers,
            peers_by_uuid=self._peers_by_uuid,
            gateway_url=self.gateway_url,
            port=self.port,
            on_request=self._handle_a2a_request,
        )
        await self._server.start()

        logger.info("Gateway HTTP server started on port %d", self.port)

    async def _fetch_all_peers_with_retry(self) -> list[Peer]:
        """Fetch all peer pages, retrying if the platform rate-limits startup."""
        all_peers: list[Peer] = []
        page = 1
        page_size = 100

        while True:
            response = await self._list_peers_page_with_retry(
                page=page,
                page_size=page_size,
            )
            all_peers.extend(response.data)

            if len(response.data) < page_size:
                return all_peers
            page += 1

    async def _list_peers_page_with_retry(
        self, *, page: int, page_size: int
    ) -> ListAgentPeersResponse:
        """Fetch one peer page with explicit backoff for live 429s."""
        attempts = len(self._peer_discovery_retry_delays_seconds) + 1
        for attempt, delay in enumerate(
            (0.0, *self._peer_discovery_retry_delays_seconds), start=1
        ):
            if delay > 0:
                logger.warning(
                    "Rate limited discovering peers for gateway; retrying page %s in %.1fs "
                    "(attempt %s/%s)",
                    page,
                    delay,
                    attempt,
                    attempts,
                )
                await asyncio.sleep(delay)

            try:
                return await self._rest.agent_api_peers.list_agent_peers(
                    page=page,
                    page_size=page_size,
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
            except ApiError as exc:
                if exc.status_code != 429 or attempt == attempts:
                    raise

        raise RuntimeError("Peer discovery retry loop exited unexpectedly")

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: GatewaySessionState,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        """Receive Thenvoi response, correlate with pending A2A task.

        This is called when a peer responds in a room. We correlate the
        response with the pending A2A task and stream it back via SSE.

        Note: We don't use `tools` here - all operations use self._rest.
        The tools parameter is room-bound and we need room-specific operations.

        Args:
            msg: Platform message from peer.
            tools: Agent tools (not used - we use REST client).
            history: Converted history as GatewaySessionState.
            participants_msg: Participants update message, or None.
            contacts_msg: Contact changes broadcast message, or None.
            is_session_bootstrap: True if this is first message from room.
            room_id: The room identifier.
        """
        # Rehydrate on bootstrap
        if is_session_bootstrap and history:
            self._rehydrate(history)

        # Find pending task for this room
        pending = self._pending_tasks.get(room_id)
        if pending:
            # Convert to A2A event and push to SSE queue
            event = self._translate_to_a2a(msg, pending.task)
            await pending.sse_queue.put(event)

            # Clean up on terminal state
            if event.final:
                del self._pending_tasks[room_id]

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up resources for a room.

        Args:
            room_id: The room identifier.
        """
        # Clean up pending task if exists
        self._pending_tasks.pop(room_id, None)
        logger.debug("Cleaned up gateway resources for room %s", room_id)

    async def stop(self) -> None:
        """Stop the HTTP server and clean up resources."""
        if self._server:
            await self._server.stop()
            self._server = None
        logger.info("Gateway adapter stopped")

    def _resolve_peer(self, peer_id: str) -> Peer | None:
        """Resolve peer by slug or UUID.

        Args:
            peer_id: Peer slug or UUID.

        Returns:
            Peer if found, None otherwise.
        """
        # Try slug first (primary)
        if peer_id in self._peers:
            return self._peers[peer_id]
        # Try UUID fallback
        return self._peers_by_uuid.get(peer_id)

    async def _handle_a2a_request(
        self, peer_id: str, message: A2AMessage
    ) -> AsyncIterator[TaskStatusUpdateEvent]:
        """Handle incoming A2A request from remote agent.

        Args:
            peer_id: Target peer slug or UUID.
            message: A2A message from remote agent.

        Yields:
            TaskStatusUpdateEvent for SSE streaming.
        """
        # Resolve peer from slug or UUID
        peer = self._resolve_peer(peer_id)
        if not peer:
            logger.error("Peer not found: %s", peer_id)
            return

        # Use the peer's actual UUID for Thenvoi API calls
        peer_uuid = peer.id

        # Get or create room for context
        room_id, context_id = await self._get_or_create_room(
            message.context_id, peer_uuid
        )

        # Create A2A task
        task = self._create_task(context_id)

        # Register pending task with SSE queue
        sse_queue: asyncio.Queue[TaskStatusUpdateEvent] = asyncio.Queue()
        self._pending_tasks[room_id] = PendingA2ATask(
            task=task,
            sse_queue=sse_queue,
            peer_id=peer_uuid,
        )

        # Emit task event to track context mapping in history
        await self._emit_context_event(room_id, context_id)

        # Send message to Thenvoi via REST client
        content = get_message_text(message) or ""

        # Use peer name for mention
        peer_name = peer.name

        await self._rest.agent_api_messages.create_agent_chat_message(
            chat_id=room_id,
            message=ChatMessageRequest(
                content=f"@{peer_name} {content}",
                mentions=[ChatMessageRequestMentionsItem(id=peer_uuid, name=peer_name)],
            ),
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

        logger.debug(
            "Sent message to peer %s (%s) in room %s (context=%s)",
            peer_name,
            peer_uuid,
            room_id,
            context_id,
        )

        # Stream events from queue (populated by on_message())
        while True:
            event = await sse_queue.get()
            yield event
            if event.final:
                break

    async def _get_or_create_room(
        self, context_id: str | None, target_peer_id: str
    ) -> tuple[str, str]:
        """Get existing room for context or create a new one.

        Args:
            context_id: A2A context ID (may be None for new conversations).
            target_peer_id: Target peer to add to room.

        Returns:
            Tuple of (room_id, context_id).
        """
        # New or None context_id → create new room
        if context_id is None or context_id not in self._context_to_room:
            # Create new room via REST
            response = await self._rest.agent_api_chats.create_agent_chat(
                chat=ChatRoomRequest(),
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            room_id = response.data.id

            # Add target peer to room
            await self._rest.agent_api_participants.add_agent_chat_participant(
                chat_id=room_id,
                participant=ParticipantRequest(
                    participant_id=target_peer_id, role="member"
                ),
                request_options=DEFAULT_REQUEST_OPTIONS,
            )

            context_id = context_id or str(uuid4())
            self._context_to_room[context_id] = room_id
            self._room_participants[room_id] = {target_peer_id}

            logger.info(
                "Created new room %s for context %s with peer %s",
                room_id,
                context_id,
                target_peer_id,
            )
        else:
            # Existing context → use existing room
            room_id = self._context_to_room[context_id]

            # Same context, different peer → add to room (multi-agent conversation)
            if target_peer_id not in self._room_participants.get(room_id, set()):
                await self._rest.agent_api_participants.add_agent_chat_participant(
                    chat_id=room_id,
                    participant=ParticipantRequest(
                        participant_id=target_peer_id, role="member"
                    ),
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
                self._room_participants.setdefault(room_id, set()).add(target_peer_id)

                logger.info(
                    "Added peer %s to existing room %s (context=%s)",
                    target_peer_id,
                    room_id,
                    context_id,
                )

        return room_id, context_id

    def _rehydrate(self, history: GatewaySessionState) -> None:
        """Restore session state from history.

        Args:
            history: Session state extracted from platform history.
        """
        # Restore context → room mappings
        for context_id, room_id in history.context_to_room.items():
            if context_id not in self._context_to_room:
                self._context_to_room[context_id] = room_id
                logger.debug("Restored context mapping: %s → %s", context_id, room_id)

        # Restore room participants
        for room_id, participants in history.room_participants.items():
            existing = self._room_participants.get(room_id, set())
            self._room_participants[room_id] = existing | participants

        logger.info(
            "Rehydrated gateway state: %d contexts, %d rooms",
            len(self._context_to_room),
            len(self._room_participants),
        )

    def _create_task(self, context_id: str) -> Task:
        """Create a new A2A Task for tracking.

        Args:
            context_id: A2A context ID.

        Returns:
            New Task instance.
        """
        return Task(
            id=str(uuid4()),
            context_id=context_id,
            status=TaskStatus(state=TaskState.working),
        )

    def _translate_to_a2a(
        self, msg: PlatformMessage, task: Task
    ) -> TaskStatusUpdateEvent:
        """Convert platform message to A2A TaskStatusUpdateEvent.

        Args:
            msg: Platform message from peer.
            task: Associated A2A task.

        Returns:
            TaskStatusUpdateEvent for SSE streaming.
        """
        # Determine task state based on message type
        message_type = getattr(msg, "message_type", "text")

        if message_type == "error":
            state = TaskState.failed
            final = True
        elif message_type in ("thought", "tool_call", "tool_result"):
            state = TaskState.working
            final = False
        else:
            # Regular text message = completed response
            state = TaskState.completed
            final = True

        # Update task status
        task.status = TaskStatus(
            state=state,
            message=A2AMessage(
                role=Role.agent,
                message_id=str(uuid4()),
                parts=[Part(root=TextPart(text=msg.content))],
            ),
        )

        return TaskStatusUpdateEvent(
            task_id=task.id,
            context_id=task.context_id,
            status=task.status,
            final=final,
        )

    async def _emit_context_event(self, room_id: str, context_id: str) -> None:
        """Emit a task event to persist context mapping in history.

        This enables session rehydration when the agent rejoins.

        Args:
            room_id: The room ID.
            context_id: The A2A context ID.
        """
        await self._rest.agent_api_events.create_agent_chat_event(
            chat_id=room_id,
            event=ChatEventRequest(
                content="A2A gateway context",
                message_type="task",
                metadata={
                    "gateway_context_id": context_id,
                    "gateway_room_id": room_id,
                },
            ),
        )
