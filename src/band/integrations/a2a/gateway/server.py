"""HTTP server for the A2A Gateway adapter."""

from __future__ import annotations

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
from a2a.utils.constants import PROTOCOL_VERSION_0_3, PROTOCOL_VERSION_CURRENT
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from band.integrations.uvicorn_server import (
    SERVER_START_TIMEOUT_S,
    SERVER_STOP_TIMEOUT_S,
    ManagedUvicornServer,
)
from band_rest import Peer

logger = logging.getLogger(__name__)

ExecutorFactory = Callable[[str], AgentExecutor]

# sse_starlette's shutdown-drain footgun (see uvicorn_server's docstring)
# is disabled by importing that module, not here.

# The REST endpoints the gateway serves per peer: the messaging binding and
# the compat card. The upstream factory also returns task read/cancel/list
# and push-config routes — an unauthenticated window into past conversations.
MESSAGING_REST_SUFFIXES = ("/message:send", "/message:stream", "/card")

# JSON-RPC methods the gateway serves (1.0 + v0.3-compat spellings). Sends
# create work; per-task ops are gated by the unguessable task UUID. Everything
# else stays closed -- with no auth layer, enumeration/push-config methods
# would disclose or disrupt other callers' conversations.
ALLOWED_JSONRPC_METHODS = frozenset(
    {
        "SendMessage",
        "SendStreamingMessage",
        "GetTask",
        "CancelTask",
        "SubscribeToTask",
        "message/send",
        "message/stream",
        "tasks/get",
        "tasks/cancel",
        "tasks/resubscribe",
    }
)


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
        self._runtime: ManagedUvicornServer | None = None

    def _agent_card(self, slug: str, peer: Peer) -> AgentCard:
        rpc_url = f"{self.gateway_url}/agents/{slug}"
        return AgentCard(
            name=peer.name,
            description=peer.description or "",
            supported_interfaces=[
                AgentInterface(
                    protocol_binding="JSONRPC",
                    protocol_version=PROTOCOL_VERSION_CURRENT,
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
                protocol_routes.extend(self._guarded_jsonrpc_routes(handler, alias))
                rest_routes.extend(self._messaging_rest_routes(handler, alias))

        return Starlette(routes=routes + protocol_routes + rest_routes)

    @staticmethod
    def _guarded_jsonrpc_routes(
        handler: DefaultRequestHandler, alias: str
    ) -> list[BaseRoute]:
        """The JSON-RPC binding, with non-messaging methods closed off.

        The upstream dispatcher serves the full method set, and with no auth
        layer every caller shares one task-store identity — see
        ``ALLOWED_JSONRPC_METHODS`` for what stays open and why.
        """
        (route,) = create_jsonrpc_routes(
            handler,
            rpc_url=f"/agents/{alias}",
            enable_v0_3_compat=True,
        )
        dispatch = route.endpoint

        async def endpoint(request: Request) -> Any:
            try:
                body = await request.json()
            except Exception:
                body = None
            method = body.get("method") if isinstance(body, dict) else None
            if method is not None and method not in ALLOWED_JSONRPC_METHODS:
                request_id = body.get("id") if isinstance(body, dict) else None
                if not isinstance(request_id, str | int):
                    request_id = None
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": "Method not found"},
                    }
                )
            # The request body is cached on the Request, so the dispatcher
            # can re-read it.
            return await dispatch(request)

        return [Route(f"/agents/{alias}", endpoint, methods=["POST"])]

    @staticmethod
    def _messaging_rest_routes(
        handler: DefaultRequestHandler, alias: str
    ) -> list[BaseRoute]:
        """The REST binding, reduced to the endpoints this gateway serves.

        The upstream factory also returns a catch-all ``Mount("/{tenant}")``;
        since peers are namespaced by path here, the first alias's mount
        would shadow every later alias's routes.
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

    @property
    def bound_port(self) -> int:
        """The actual listening port -- resolves ``port=0`` to whatever the
        OS assigned."""
        if self._runtime is None:
            raise RuntimeError("A2A Gateway server has not started")
        return self._runtime.bound_port

    async def start(self) -> None:
        self._app = self._build_app()
        self._runtime = ManagedUvicornServer(
            self._app,
            host="0.0.0.0",
            port=self.port,
            start_timeout_s=SERVER_START_TIMEOUT_S,
            stop_timeout_s=SERVER_STOP_TIMEOUT_S,
        )
        await self._runtime.start()
        logger.info(
            "Starting A2A Gateway server on port %d with %d peers",
            self.port,
            len(self.peers),
        )

    async def stop(self) -> None:
        if self._runtime is None:
            return
        await self._runtime.stop()
        self._runtime = None
        logger.info("A2A Gateway server stopped")
