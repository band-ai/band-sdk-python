"""HTTP server for A2A Gateway adapter."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Message as A2AMessage,
    Task,
    TaskStatusUpdateEvent,
)
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from thenvoi_rest import Peer

logger = logging.getLogger(__name__)

# Type alias for the on_request callback
OnRequestCallback = Callable[[str, A2AMessage], AsyncIterator[TaskStatusUpdateEvent]]


class GatewayServer:
    """Starlette HTTP server exposing A2A endpoints for each peer.

    Creates per-peer routes for AgentCard discovery and message streaming.
    Routes are created at startup based on the discovered peers.

    Peers are addressed by slug (e.g., "weather-agent") with UUID fallback.

    Attributes:
        peers: Dict mapping slug to Peer objects (primary lookup).
        peers_by_uuid: Dict mapping UUID to Peer objects (fallback lookup).
        gateway_url: Base URL for the gateway (used in AgentCard URLs).
        port: Port to listen on.
        on_request: Callback invoked when an A2A message is received.
    """

    def __init__(
        self,
        peers: dict[str, Peer],
        peers_by_uuid: dict[str, Peer],
        gateway_url: str,
        port: int,
        on_request: OnRequestCallback,
    ) -> None:
        """Initialize gateway server.

        Args:
            peers: Dict of slug → Peer objects (primary lookup).
            peers_by_uuid: Dict of UUID → Peer objects (fallback lookup).
            gateway_url: Base URL for AgentCard URLs (e.g., "http://localhost:10000").
            port: Port to listen on.
            on_request: Async callback invoked with (peer_id, message) → events.
        """
        self.peers = peers
        self.peers_by_uuid = peers_by_uuid
        self.gateway_url = gateway_url
        self.port = port
        self.on_request = on_request
        self._app: Starlette | None = None
        self._server_task: asyncio.Task[Any] | None = None

    def _resolve_peer(self, peer_id: str) -> tuple[str, Peer] | None:
        """Resolve peer by slug or UUID.

        Args:
            peer_id: Peer slug or UUID from URL path.

        Returns:
            Tuple of (slug, Peer) if found, None otherwise.
        """
        # Try slug first (primary)
        if peer_id in self.peers:
            return peer_id, self.peers[peer_id]
        # Try UUID fallback
        if peer_id in self.peers_by_uuid:
            peer = self.peers_by_uuid[peer_id]
            # Find the slug for this peer
            for slug, p in self.peers.items():
                if p.id == peer.id:
                    return slug, peer
        return None

    def _build_app(self) -> Starlette:
        """Build Starlette application with routes."""
        routes = [
            # List all available peers
            Route(
                "/peers",
                self._handle_list_peers,
                methods=["GET"],
            ),
            # Per-peer agent card discovery (support both naming conventions)
            Route(
                "/agents/{peer_id}/.well-known/agent.json",
                self._handle_agent_card,
                methods=["GET"],
            ),
            Route(
                "/agents/{peer_id}/.well-known/agent-card.json",
                self._handle_agent_card,
                methods=["GET"],
            ),
            # Per-peer message streaming (legacy REST endpoint)
            Route(
                "/agents/{peer_id}/v1/message:stream",
                self._handle_message_stream,
                methods=["POST"],
            ),
            # JSON-RPC endpoint (A2A SDK posts here with method field)
            Route(
                "/agents/{peer_id}",
                self._handle_jsonrpc,
                methods=["POST"],
            ),
        ]
        return Starlette(routes=routes)

    async def _handle_list_peers(self, request: Request) -> JSONResponse:
        """Return list of all available peers.

        Args:
            request: Starlette request.

        Returns:
            JSONResponse with list of peers (slug, id, name, description).
        """
        peers_list = [
            {
                "slug": slug,  # Primary identifier for URLs
                "id": peer.id,  # UUID fallback
                "name": peer.name,  # Display name
                "description": peer.description or "",
            }
            for slug, peer in self.peers.items()
        ]
        return JSONResponse({"peers": peers_list, "count": len(peers_list)})

    async def _handle_agent_card(self, request: Request) -> JSONResponse:
        """Return AgentCard for the specified peer.

        Args:
            request: Starlette request with peer_id path parameter (slug or UUID).

        Returns:
            JSONResponse with AgentCard or 404 if peer not found.
        """
        peer_id = request.path_params["peer_id"]
        resolved = self._resolve_peer(peer_id)
        if not resolved:
            return JSONResponse({"error": "Not found"}, status_code=404)

        slug, peer = resolved

        card = AgentCard(
            name=peer.name,
            description=peer.description or "",
            url=f"{self.gateway_url}/agents/{slug}",  # Use slug in URL
            version="1.0.0",
            capabilities=AgentCapabilities(streaming=True),
            skills=[
                AgentSkill(
                    id="default",
                    name=peer.name,
                    description=peer.description or "",
                    tags=["band", "gateway"],
                )
            ],
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
        )
        return JSONResponse(card.model_dump(mode="json", by_alias=True))

    async def _handle_message_stream(
        self, request: Request
    ) -> JSONResponse | StreamingResponse:
        """Handle incoming A2A message and stream response events.

        Args:
            request: Starlette request with peer_id path parameter (slug or UUID) and JSON body.

        Returns:
            StreamingResponse with SSE events.
        """
        peer_id = request.path_params["peer_id"]

        # Resolve peer by slug or UUID
        resolved = self._resolve_peer(peer_id)
        if not resolved:
            return JSONResponse({"error": "Not found"}, status_code=404)

        slug, peer = resolved

        body = await request.json()
        try:
            message = A2AMessage(**body)
        except ValidationError as exc:
            return JSONResponse(
                {
                    "error": "Invalid A2A message payload",
                    "details": exc.errors(),
                },
                status_code=400,
            )

        logger.debug(
            "Received A2A message for peer %s (%s): %s",
            peer.name,
            slug,
            message.message_id,
        )

        async def event_stream() -> AsyncIterator[str]:
            """Generate SSE events from on_request callback."""
            try:
                # Pass slug to callback (adapter resolves to peer)
                async for event in self.on_request(slug, message):
                    yield f"data: {event.model_dump_json()}\n\n"
            except Exception as e:
                logger.exception("Error in A2A request handler: %s", e)
                # Send error event
                yield f'data: {{"error": "{e!s}"}}\n\n'

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    async def _handle_jsonrpc(
        self, request: Request
    ) -> JSONResponse | StreamingResponse:
        """Handle JSON-RPC requests from A2A SDK.

        The A2A SDK posts JSON-RPC requests to the base agent URL with
        a method field to indicate the operation (message/send, message/stream, etc.).

        Args:
            request: Starlette request with peer_id path parameter and JSON-RPC body.

        Returns:
            JSONResponse for sync methods, StreamingResponse for streaming methods.
        """
        peer_id = request.path_params["peer_id"]

        # Resolve peer by slug or UUID
        resolved = self._resolve_peer(peer_id)
        if not resolved:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32001, "message": "Peer not found"},
                    "id": None,
                },
                status_code=404,
            )

        slug, peer = resolved
        body = await request.json()

        # Extract JSON-RPC fields
        method = body.get("method", "")
        request_id = body.get("id")
        params = body.get("params", {})

        logger.debug(
            "Received JSON-RPC %s for peer %s (%s), request_id=%s",
            method,
            peer.name,
            slug,
            request_id,
        )

        # Route based on method
        if method == "message/send":
            return await self._handle_jsonrpc_send(slug, params, request_id)
        elif method == "message/stream":
            return await self._handle_jsonrpc_stream(slug, params, request_id)
        else:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                    "id": request_id,
                },
                status_code=400,
            )

    async def _handle_jsonrpc_send(
        self, slug: str, params: dict[str, Any], request_id: str | None
    ) -> JSONResponse:
        """Handle synchronous message/send JSON-RPC request.

        Args:
            slug: Peer slug.
            params: JSON-RPC params containing message.
            request_id: JSON-RPC request ID.

        Returns:
            JSONResponse with JSON-RPC result (Task).
        """
        # Extract message from params
        message_data = params.get("message", {})
        try:
            message = A2AMessage(**message_data)
        except ValidationError as exc:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32602,
                        "message": "Invalid params",
                        "data": exc.errors(),
                    },
                    "id": request_id,
                },
                status_code=400,
            )

        # Collect all events until final
        final_event: TaskStatusUpdateEvent | None = None
        try:
            async for event in self.on_request(slug, message):
                final_event = event
                if event.final:
                    break
        except Exception as e:
            logger.exception("Error in JSON-RPC send handler: %s", e)
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32000, "message": str(e)},
                    "id": request_id,
                },
                status_code=500,
            )

        # Build Task result from final event
        if final_event:
            task = Task(
                id=final_event.task_id,
                context_id=final_event.context_id,
                status=final_event.status,
            )
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "result": task.model_dump(mode="json", by_alias=True),
                    "id": request_id,
                }
            )
        else:
            # No events received - create empty task
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32000, "message": "No response from peer"},
                    "id": request_id,
                },
                status_code=500,
            )

    async def _handle_jsonrpc_stream(
        self, slug: str, params: dict[str, Any], request_id: str | None
    ) -> StreamingResponse:
        """Handle streaming message/stream JSON-RPC request.

        Args:
            slug: Peer slug.
            params: JSON-RPC params containing message.
            request_id: JSON-RPC request ID.

        Returns:
            StreamingResponse with SSE events in JSON-RPC format.
        """
        # Extract message from params
        message_data = params.get("message", {})
        try:
            message = A2AMessage(**message_data)
        except ValidationError as exc:
            return StreamingResponse(
                iter(
                    [
                        "data: "
                        + json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "error": {
                                    "code": -32602,
                                    "message": "Invalid params",
                                    "data": exc.errors(),
                                },
                                "id": request_id,
                            }
                        )
                        + "\n\n"
                    ]
                ),
                media_type="text/event-stream",
                status_code=400,
            )

        async def event_stream() -> AsyncIterator[str]:
            """Generate SSE events in JSON-RPC format."""
            try:
                async for event in self.on_request(slug, message):
                    # Wrap event in JSON-RPC response
                    jsonrpc_response = {
                        "jsonrpc": "2.0",
                        "result": event.model_dump(mode="json", by_alias=True),
                        "id": request_id,
                    }
                    yield f"data: {json.dumps(jsonrpc_response)}\n\n"
            except Exception as e:
                logger.exception("Error in JSON-RPC stream handler: %s", e)
                error_response = {
                    "jsonrpc": "2.0",
                    "error": {"code": -32000, "message": str(e)},
                    "id": request_id,
                }
                yield f"data: {json.dumps(error_response)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    async def start(self) -> None:
        """Start the HTTP server.

        Creates the Starlette app and starts serving on the configured port.
        """
        import uvicorn

        self._app = self._build_app()

        config = uvicorn.Config(
            self._app,
            host="0.0.0.0",
            port=self.port,
            log_level="warning",
        )
        server = uvicorn.Server(config)

        logger.info(
            "Starting A2A Gateway server on port %d with %d peers",
            self.port,
            len(self.peers),
        )

        # Run server in background task
        self._server_task = asyncio.create_task(server.serve())

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
            self._server_task = None
            logger.info("A2A Gateway server stopped")
