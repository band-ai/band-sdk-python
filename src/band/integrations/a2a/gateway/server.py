"""HTTP server for the A2A Gateway adapter."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from starlette.applications import Starlette
from starlette.routing import BaseRoute, Route

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
            supported_interfaces=[
                AgentInterface(
                    protocol_binding="JSONRPC",
                    protocol_version="1.0",
                    url=rpc_url,
                )
            ],
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

    def _build_app(self) -> Starlette:
        routes: list[BaseRoute] = [
            Route("/peers", self._handle_list_peers, methods=["GET"]),
        ]
        protocol_routes: list[BaseRoute] = []
        rest_routes: list[BaseRoute] = []

        for slug, peer in self.peers.items():
            aliases = dict.fromkeys((slug, peer.id))
            for alias in aliases:
                card = self._agent_card(alias, peer)
                executor = self.executor_factory(slug)
                handler = DefaultRequestHandler(
                    agent_executor=executor,
                    task_store=InMemoryTaskStore(),
                    agent_card=card,
                )
                protocol_routes.extend(
                    create_agent_card_routes(
                        card,
                        card_url=f"/agents/{alias}/.well-known/agent-card.json",
                    )
                )
                protocol_routes.extend(
                    create_agent_card_routes(
                        card,
                        card_url=f"/agents/{alias}/.well-known/agent.json",
                    )
                )
                protocol_routes.extend(
                    create_jsonrpc_routes(
                        handler,
                        rpc_url=f"/agents/{alias}",
                        enable_v0_3_compat=True,
                    )
                )
                rest_routes.extend(
                    create_rest_routes(
                        handler,
                        enable_v0_3_compat=True,
                        path_prefix=f"/agents/{alias}",
                    )
                )

        return Starlette(routes=routes + protocol_routes + rest_routes)

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
