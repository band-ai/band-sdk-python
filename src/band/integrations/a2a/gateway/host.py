"""Foreground A2A gateway host."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from band.core.exceptions import LifecycleError
from band.core.gateways import GatewayBase, stop_all
from band.integrations.a2a.gateway.adapter import A2AGatewayAdapter
from band.integrations.a2a.gateway.server import GatewayServer

if TYPE_CHECKING:
    from band.agent import Agent

logger = logging.getLogger(__name__)


class A2AGateway(GatewayBase[A2AGatewayAdapter]):
    """Owns an :class:`~band.agent.Agent` with :class:`A2AGatewayAdapter`.

    The gateway starts the agent (peer discovery, HTTP app construction) and
    runs the HTTP server in the foreground via :meth:`serve`.

    Example::

        agent = Agent.create(adapter=A2AGatewayAdapter(...), ...)
        async with A2AGateway(agent=agent) as gateway:
            await gateway.serve()
    """

    def __init__(self, agent: Agent) -> None:
        super().__init__(agent)
        _ = self.adapter  # validate adapter type at construction
        self._server: GatewayServer | None = None

    async def _start_resources(self) -> None:
        adapter = self.adapter
        self._override(adapter, "manage_http_server", False)
        await self._agent.start()

        server = adapter.http_server
        if server is None:
            raise LifecycleError("A2A gateway adapter did not prepare HTTP server")

        self._server = server
        logger.info("A2A gateway agent started; HTTP server ready for serve()")

    async def _stop_resources(self) -> None:
        await stop_all(
            (self._stop_http, self._stop_agent),
            "A2A gateway stop failed",
        )

    async def _stop_http(self) -> None:
        if self._server is not None:
            await self._server.stop()
            self._server = None

    async def _serve_transport(self) -> None:
        if self._server is None:
            raise LifecycleError("A2A gateway HTTP server is not prepared")
        await self._server.serve()
