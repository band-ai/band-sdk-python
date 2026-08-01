"""Foreground ACP gateway host.

Manual ``await agent.start(); await run_agent(server)`` (or
``async with agent: await run_agent(server)``) is deprecated in favor of
:class:`ACPGateway` with :meth:`serve`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from band.core.gateways import GatewayBase, stop_all
from band.integrations.acp.server import ACPServer
from band.integrations.acp.server_adapter import BandACPServerAdapter

if TYPE_CHECKING:
    from band.agent import Agent

logger = logging.getLogger(__name__)


class ACPGateway(GatewayBase[BandACPServerAdapter]):
    """Owns an :class:`~band.agent.Agent` with :class:`BandACPServerAdapter`.

    The gateway starts the agent (WebSocket connection) and runs the ACP
    stdio/TCP transport in the foreground via :meth:`serve`.

    Wire push handlers, routers, and other adapter configuration before
    constructing the gateway; it only owns lifecycle and transport serving.

    Example::

        adapter = BandACPServerAdapter(...)
        push_handler = ACPPushHandler(adapter)
        adapter.set_push_handler(push_handler)
        server = ACPServer(adapter)
        agent = Agent.create(adapter=adapter, ...)
        async with ACPGateway(agent=agent, server=server) as gateway:
            await gateway.serve()
    """

    def __init__(self, agent: Agent, *, server: ACPServer | None = None) -> None:
        super().__init__(agent)
        self._server = server if server is not None else ACPServer(self.adapter)

    async def _start_resources(self) -> None:
        await self._agent.start()
        logger.info("ACP gateway agent started; transport ready for serve()")

    async def _stop_resources(self) -> None:
        await stop_all(
            (self._stop_agent, self.adapter.close),
            "ACP gateway stop failed",
        )

    async def _serve_transport(self) -> None:
        from acp import run_agent

        await run_agent(self._server)
