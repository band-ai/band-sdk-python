"""A2A Gateway Adapter that exposes Band peers as A2A endpoints."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import partial
import logging
import re
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import ClassVar
from uuid import uuid4

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import Task, TaskState, TaskStatus

from band.client.rest import (
    AsyncRestClient,
    ChatEventRequest,
    ChatMessageRequest,
    ChatMessageRequestMentionsItem,
    ChatRoomRequest,
    DEFAULT_REQUEST_OPTIONS,
    ParticipantRequest,
)
from band.converters.a2a_gateway import GatewayHistoryConverter
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AdapterFeatures, Capability, Emit, PlatformMessage
from band.integrations.a2a.gateway.server import GatewayServer
from band.integrations.a2a.gateway.config import A2AGatewayAdapterConfig
from band.integrations.a2a.gateway.types import GatewaySessionState, PendingA2ATask
from band.integrations.a2a.protocol import snapshot_task
from band_rest import Peer

logger = logging.getLogger(__name__)


@dataclass
class GatewayRequest:
    """Band routing and A2A state for one gateway request."""

    peer: Peer
    room_id: str
    context_id: str
    pending: PendingA2ATask


class BandAgentExecutor(AgentExecutor):
    """Adapt one official A2A handler execution to a Band peer."""

    def __init__(self, adapter: A2AGatewayAdapter, peer_slug: str) -> None:
        self.adapter = adapter
        self.peer_slug = peer_slug

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        await self.adapter._execute_a2a(self.peer_slug, context, event_queue)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await self.adapter._cancel_a2a(context, event_queue)


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
    """Gateway adapter exposing Band peers as A2A endpoints.

    This adapter enables remote A2A agents to interact with Band platform
    peers through standard A2A HTTP endpoints. It acts as a bridge:
    - Receives A2A messages via HTTP server
    - Creates/reuses Band chat rooms for context management
    - Sends messages to peers via REST API
    - Streams responses back via SSE

    Uses direct REST client (not AgentToolsProtocol) because:
    - AgentToolsProtocol is room-bound (passed in on_message with room context)
    - Gateway receives HTTP requests outside of on_message() context
    - Gateway needs to send messages to SPECIFIC rooms

    Example:
        from band import Agent
        from band.integrations.a2a.gateway import A2AGatewayAdapter

        adapter = A2AGatewayAdapter(
            rest_url="https://app.band.ai",
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
        rest_url: str = "https://app.band.ai",
        api_key: str = "",
        gateway_url: str = "http://localhost:10000",
        port: int = 10000,
        config: A2AGatewayAdapterConfig | None = None,
        features: AdapterFeatures | None = None,
    ) -> None:
        """Initialize gateway adapter.

        Args:
            rest_url: Base URL for Band REST API.
            api_key: API key for authentication (same as Agent.create()).
            gateway_url: Base URL for A2A endpoints exposed by this gateway.
            port: Port for HTTP server to listen on.
            config: A2A Gateway runtime configuration.
        """
        super().__init__(
            history_converter=GatewayHistoryConverter(),
            features=features,
        )
        self.gateway_url = gateway_url
        self.port = port
        self.config = config or A2AGatewayAdapterConfig()

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

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Fetch peers via REST and start HTTP server.

        Args:
            agent_name: Name of this agent.
            agent_description: Description of this agent.
        """
        await super().on_started(agent_name, agent_description)

        # Fetch ALL peers at startup using REST client (with pagination)
        all_peers = await self._fetch_all_peers()

        # Build slug and UUID mappings
        for peer in all_peers:
            slug = slugify(peer.name)
            self._peers[slug] = peer
            self._peers_by_uuid[peer.id] = peer

        logger.info("Discovered %d peers for gateway", len(self._peers))

        # Create and start HTTP server with peer routes
        self._server = GatewayServer(
            peers=self._peers,
            gateway_url=self.gateway_url,
            port=self.port,
            executor_factory=partial(BandAgentExecutor, self),
        )
        await self._server.start()

        logger.info("Gateway HTTP server started on port %d", self.port)

    async def _fetch_all_peers(self) -> list[Peer]:
        """Fetch every peer page using the REST client's retry policy."""
        all_peers: list[Peer] = []
        page = 1
        page_size = 100

        while True:
            response = await self._rest.agent_api_peers.list_agent_peers(
                page=page,
                page_size=page_size,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            all_peers.extend(response.data)

            if len(response.data) < page_size:
                return all_peers
            page += 1

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
        """Receive Band response, correlate with pending A2A task.

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

        # Find pending task for this room.
        pending = self._pending_tasks.get(room_id)
        if pending:
            logger.debug(
                "A2A response received: room=%s task=%s type=%s",
                room_id,
                pending.task.id,
                msg.message_type,
            )
            await self._publish_band_response(pending, msg)
        else:
            logger.debug(
                "Ignoring Band message without pending A2A task: room=%s", room_id
            )

    async def on_cleanup(self, room_id: str) -> None:
        """Clean up resources for a room.

        Args:
            room_id: The room identifier.
        """
        pending = self._pending_tasks.pop(room_id, None)
        if pending:
            await pending.fail("Band room closed before the A2A response completed")
        logger.debug("Cleaned up gateway resources for room %s", room_id)

    async def cleanup_all(self) -> None:
        """Fail in-flight requests and stop the self-hosted HTTP server."""
        for room_id in list(self._pending_tasks):
            pending = self._pending_tasks.pop(room_id)
            await pending.fail("Gateway shut down before the Band response completed")
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

    def _make_task(self, context: RequestContext) -> Task:
        """Create the task event emitted before Band execution starts."""
        return Task(
            id=context.task_id,
            context_id=context.context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )

    async def _execute_a2a(
        self, peer_id: str, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Bridge one official A2A execution to a Band room."""
        try:
            request = await self._establish_request(peer_id, context, event_queue)
        except Exception:
            logger.exception(
                "A2A request setup failed: peer=%s context=%s",
                peer_id,
                context.context_id,
            )
            raise
        logger.info(
            "A2A request started: peer=%s room=%s context=%s task=%s",
            peer_id,
            request.room_id,
            request.context_id,
            request.pending.task.id,
        )
        try:
            async with self.pending_task(request.room_id, request.pending):
                await self._announce_request(request)
                await self._send_to_band(request, context)
                completed = await self._await_response(request)
        except asyncio.CancelledError:
            logger.debug(
                "A2A request cancelled: room=%s task=%s",
                request.room_id,
                request.pending.task.id,
            )
            raise
        except Exception:
            logger.exception(
                "A2A request failed: room=%s context=%s task=%s",
                request.room_id,
                request.context_id,
                request.pending.task.id,
            )
            raise
        else:
            if completed:
                logger.info(
                    "A2A request completed: room=%s task=%s",
                    request.room_id,
                    request.pending.task.id,
                )

    async def _establish_request(
        self, peer_id: str, context: RequestContext, event_queue: EventQueue
    ) -> GatewayRequest:
        """Resolve the peer and create its Band room and A2A task state."""
        peer = self._resolve_peer(peer_id)
        if not peer:
            logger.warning("A2A request target not found: peer=%s", peer_id)
            raise ValueError(f"Peer not found: {peer_id}")

        room_id, context_id = await self._get_or_create_room(
            context.context_id, peer.id
        )
        task = self._make_task(context)
        return GatewayRequest(
            peer=peer,
            room_id=room_id,
            context_id=context_id,
            pending=PendingA2ATask(task=task, event_queue=event_queue),
        )

    async def _announce_request(self, request: GatewayRequest) -> None:
        """Publish the initial working task and retain its Band context."""
        await request.pending.event_queue.enqueue_event(
            snapshot_task(request.pending.task)
        )
        await self._emit_context_event(request.room_id, request.context_id)

    async def _send_to_band(
        self, request: GatewayRequest, context: RequestContext
    ) -> None:
        """Send the A2A request text to the selected Band peer."""
        content = context.get_user_input()
        await self._rest.agent_api_messages.create_agent_chat_message(
            chat_id=request.room_id,
            message=ChatMessageRequest(
                content=f"@{request.peer.name} {content}",
                mentions=[
                    ChatMessageRequestMentionsItem(
                        id=request.peer.id, name=request.peer.name
                    )
                ],
            ),
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        logger.debug(
            "A2A request sent to Band: room=%s task=%s",
            request.room_id,
            request.pending.task.id,
        )

    async def _await_response(self, request: GatewayRequest) -> bool:
        """Wait for a terminal Band reply; False when it timed out instead."""
        try:
            if self.config.response_timeout_s is None:
                await request.pending.done.wait()
            else:
                async with asyncio.timeout(self.config.response_timeout_s):
                    await request.pending.done.wait()
        except TimeoutError:
            logger.warning(
                "A2A response timed out: room=%s task=%s timeout=%ss",
                request.room_id,
                request.pending.task.id,
                self.config.response_timeout_s,
            )
            await request.pending.fail("Timed out waiting for a Band response")
            return False
        return True

    @asynccontextmanager
    async def pending_task(
        self, room_id: str, pending: PendingA2ATask
    ) -> AsyncIterator[PendingA2ATask]:
        """Register one pending request and always release its room slot."""
        if room_id in self._pending_tasks:
            # The message reaches the remote A2A client; keep the internal
            # room id out of it.
            logger.warning("Rejected concurrent A2A request: room=%s", room_id)
            raise RuntimeError(
                "The peer is still processing a previous request for this context"
            )
        self._pending_tasks[room_id] = pending
        logger.debug(
            "Registered pending A2A task: room=%s task=%s", room_id, pending.task.id
        )
        try:
            yield pending
        finally:
            self._pending_tasks.pop(room_id, None)
            logger.debug(
                "Released pending A2A task: room=%s task=%s", room_id, pending.task.id
            )

    async def _cancel_a2a(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Publish the official terminal cancellation event."""
        task = context.current_task or self._make_task(context)
        await PendingA2ATask(task=task, event_queue=event_queue).cancel()

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

    async def _publish_band_response(
        self, pending: PendingA2ATask, msg: PlatformMessage
    ) -> None:
        """Translate Band's message category into an A2A task intent."""
        if msg.message_type == "error":
            await pending.fail(msg.content)
        elif msg.message_type in ("thought", "tool_call", "tool_result"):
            await pending.report_progress(msg.content)
        else:
            await pending.complete_with_message(msg.content)

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
