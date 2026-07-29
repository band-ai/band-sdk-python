"""HTTP server for the A2A Gateway adapter."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.compat.v0_3.conversions import to_compat_agent_card
from a2a.utils.constants import PROTOCOL_VERSION_0_3
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from band_rest import Peer

logger = logging.getLogger(__name__)

ExecutorFactory = Callable[[str], AgentExecutor]

# The REST endpoints the gateway serves per peer: the messaging binding and
# the compat card. The upstream factory also returns task read/cancel/list
# and push-config routes — an unauthenticated window into past conversations.
MESSAGING_REST_SUFFIXES = ("/message:send", "/message:stream", "/card")


class GatewayServer:
    """Expose each discovered Band peer through the official A2A server routes."""

    def __init__(
        self,
        peers: dict[str, Peer],
        gateway_url: str,
        port: int,
        executor_factory: ExecutorFactory,
    ) -> None:
        self.peers = peers
        self.gateway_url = gateway_url.rstrip("/")
        self.port = port
        self.executor_factory = executor_factory
        self._app: Starlette | None = None
        self._server_task: asyncio.Task[Any] | None = None

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
                ),
                AgentInterface(
                    protocol_binding="JSONRPC",
                    protocol_version=PROTOCOL_VERSION_0_3,
                    url=rpc_url,
                ),
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
            # One handler — and one task store — per peer, shared by all its
            # aliases, so a task started on the slug stays visible on the UUID.
            handler = DefaultRequestHandler(
                agent_executor=self.executor_factory(slug),
                task_store=InMemoryTaskStore(),
                agent_card=self._agent_card(slug, peer),
            )
            for alias in dict.fromkeys((slug, peer.id)):
                card = self._agent_card(alias, peer)
                protocol_routes.extend(
                    create_agent_card_routes(
                        card,
                        card_url=f"/agents/{alias}/.well-known/agent-card.json",
                    )
                )
                protocol_routes.append(
                    Route(
                        f"/agents/{alias}/.well-known/agent.json",
                        self._legacy_agent_card(card),
                        methods=["GET"],
                    )
                )
                protocol_routes.extend(
                    create_jsonrpc_routes(
                        handler,
                        rpc_url=f"/agents/{alias}",
                        enable_v0_3_compat=True,
                    )
                )
                rest_routes.extend(self._messaging_rest_routes(handler, alias))

        return Starlette(routes=routes + protocol_routes + rest_routes)

    @staticmethod
    def _messaging_rest_routes(
        handler: DefaultRequestHandler, alias: str
    ) -> list[BaseRoute]:
        """The REST binding, reduced to the endpoints this gateway serves.

        Beyond the unauthenticated task routes, the upstream factory ends with
        a multi-tenant catch-all ``Mount("/{tenant}")``; peers here are
        namespaced by path, and the first alias's mount would shadow every
        later alias's flat routes.
        """
        return [
            route
            for route in create_rest_routes(
                handler,
                enable_v0_3_compat=True,
                path_prefix=f"/agents/{alias}",
            )
            if isinstance(route, Route) and route.path.endswith(MESSAGING_REST_SUFFIXES)
        ]

    @staticmethod
    def _legacy_agent_card(
        card: AgentCard,
    ) -> Callable[[Any], Awaitable[JSONResponse]]:
        """Serve the SDK's v0.3 card representation for legacy discovery."""
        legacy_card = to_compat_agent_card(card)
        payload = legacy_card.model_dump(
            by_alias=True,
            mode="json",
            exclude_none=True,
        )

        async def response(_request: Any) -> JSONResponse:
            return JSONResponse(payload)

        return response

    async def _handle_list_peers(self, _request: Request) -> JSONResponse:
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
