"""Foreground Slack gateway host.

Mount-router-yourself + ``Agent.run()`` / ``agent.run_forever()`` is deprecated
in favor of :class:`SlackGateway` with :meth:`serve`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from band.core.gateways import GatewayBase, stop_all
from band.core.serving import EmbeddedServer
from band.integrations.slack.adapter import SlackAdapter
from band.integrations.slack.types import SlackTransport

if TYPE_CHECKING:
    from band.agent import Agent

logger = logging.getLogger(__name__)


class SlackGateway(GatewayBase[SlackAdapter]):
    """Owns an :class:`~band.agent.Agent` with :class:`SlackAdapter`.

    The gateway starts the agent (Band WebSocket, inner brain) and runs the
    Slack ingress transport in the foreground via :meth:`serve`:

    - ``transport="socket"`` — opens Socket Mode listeners, then blocks until
      :meth:`stop`.
    - ``transport="http"`` — serves :attr:`SlackAdapter.router` via uvicorn on
      :attr:`SlackAdapter.port`.

    Example::

        agent = Agent.create(adapter=SlackAdapter(...), ...)
        async with SlackGateway(agent=agent) as gateway:
            await gateway.serve()
    """

    def __init__(self, agent: Agent) -> None:
        super().__init__(agent)
        _ = self.adapter  # validate adapter type at construction
        self._serve_cancel: asyncio.Event | None = None
        self._http: EmbeddedServer | None = None

    async def _start_resources(self) -> None:
        adapter = self.adapter
        self._override(adapter, "manage_ingress", False)
        await self._agent.start()
        logger.info(
            "Slack gateway agent started (transport=%s); ingress deferred to serve()",
            adapter.transport,
        )

    async def _stop_resources(self) -> None:
        await stop_all(
            (self._stop_ingress, self._stop_agent),
            "Slack gateway stop failed",
        )

    async def _stop_ingress(self) -> None:
        """Release whichever transport :meth:`serve` is running."""
        if self._serve_cancel is not None:
            self._serve_cancel.set()
        if self._http is not None:
            # Awaited, not just signalled: callers restart on this port, and
            # until the run returns it is still bound and still accepting
            # Slack deliveries into an agent that is about to stop.
            await self._http.stop()
            self._http = None

    async def _serve_transport(self) -> None:
        adapter = self.adapter
        match adapter.transport:
            case SlackTransport.SOCKET:
                await self._serve_socket(adapter)
            case SlackTransport.HTTP:
                await self._serve_http(adapter)

    async def _serve_socket(self, adapter: SlackAdapter) -> None:
        async with self._cancellation_gate():
            await adapter.start_ingress()

    async def _serve_http(self, adapter: SlackAdapter) -> None:
        self._http = EmbeddedServer(
            adapter.router,
            host="0.0.0.0",
            port=adapter.port,
            log_level="info",
        )
        await self._http.serve()

    @asynccontextmanager
    async def _cancellation_gate(self) -> AsyncIterator[None]:
        """Arm a stop-signal before ingress work; block until :meth:`stop`.

        The event is created *before* yielding so a concurrent ``stop()``
        during ``start_ingress()`` cannot miss the signal. A local reference
        keeps ``wait()`` valid even if ``stop()`` clears ``_serve_cancel``.
        """
        cancel = asyncio.Event()
        self._serve_cancel = cancel
        try:
            yield
            await cancel.wait()
        finally:
            self._serve_cancel = None
