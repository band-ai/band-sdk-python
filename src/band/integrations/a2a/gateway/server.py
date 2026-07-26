"""HTTP server for the A2A Gateway adapter."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from a2a.server.agent_execution import AgentExecutor
from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication
from a2a.server.apps.rest.rest_adapter import RESTAdapter
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from starlette.applications import Starlette
from starlette.routing import Route

from band_rest import Peer

logger = logging.getLogger(__name__)

ExecutorFactory = Callable[[str], AgentExecutor]


class GatewayServer:
    """Expose each discovered Band peer through the official A2A server routes."""

    def __init__(
        self,
        peers: dict[str, Peer],
        peers_by_uuid: dict[str, Peer],
        gateway_url: str,
        port: int,
        executor_factory: ExecutorFactory,
    ) -> None:
        self.peers = peers
        self.peers_by_uuid = peers_by_uuid
        self.gateway_url = gateway_url.rstrip("/")
        self.port = port
        self.executor_factory = executor_factory
        self._app: Starlette | None = None
        self._server_task: asyncio.Task[Any] | None = None

    def _resolve_peer(self, peer_id: str) -> tuple[str, Peer] | None:
        if peer_id in self.peers:
            return peer_id, self.peers[peer_id]
        peer = self.peers_by_uuid.get(peer_id)
        if peer is None:
            return None
        return next(
            (
                (slug, candidate)
                for slug, candidate in self.peers.items()
                if candidate.id == peer.id
            ),
            None,
        )

    def _agent_card(self, slug: str, peer: Peer) -> AgentCard:
        rpc_url = f"{self.gateway_url}/agents/{slug}"
        return AgentCard(
            name=peer.name,
            description=peer.description or "",
            url=rpc_url,
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
            preferred_transport="JSONRPC",
            additional_interfaces=[AgentInterface(transport="JSONRPC", url=rpc_url)],
        )

    def _build_app(self) -> Starlette:
        routes: list[Route] = [
            Route("/peers", self._handle_list_peers, methods=["GET"]),
        ]

        for slug, peer in self.peers.items():
            card = self._agent_card(slug, peer)
            handler = DefaultRequestHandler(
                agent_executor=self.executor_factory(slug),
                task_store=InMemoryTaskStore(),
            )
            jsonrpc_app = A2AStarletteApplication(
                agent_card=card,
                http_handler=handler,
            )
            rest_adapter = RESTAdapter(agent_card=card, http_handler=handler)
            routes.extend(
                jsonrpc_app.routes(
                    agent_card_url=f"/agents/{slug}/.well-known/agent.json",
                    rpc_url=f"/agents/{slug}",
                )
            )
            routes.extend(
                Route(
                    f"/agents/{slug}{path}",
                    endpoint,
                    methods=[method],
                )
                for (path, method), endpoint in rest_adapter.routes().items()
            )

        return Starlette(routes=routes)

    async def _handle_list_peers(self, _request: Any) -> Any:
        from starlette.responses import JSONResponse

        peers = [
            {
                "slug": slug,
                "id": peer.id,
                "name": peer.name,
                "description": peer.description or "",
            }
            for slug, peer in self.peers.items()
        ]
        return JSONResponse({"peers": peers, "count": len(peers)})

    async def start(self) -> None:
        import uvicorn

        self._app = self._build_app()
        server = uvicorn.Server(
            uvicorn.Config(
                self._app, host="0.0.0.0", port=self.port, log_level="warning"
            )
        )
        self._server_task = asyncio.create_task(server.serve())
        logger.info(
            "Starting A2A Gateway server on port %d with %d peers",
            self.port,
            len(self.peers),
        )

    async def stop(self) -> None:
        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
            self._server_task = None
            logger.info("A2A Gateway server stopped")
