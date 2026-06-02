"""Health check endpoint for the bridge.

Reports aggregate health (200 = all agents connected, 503 = any disconnected)
plus per-agent connection status. The bridge holds N agents; the health
server exposes their state at ``GET /health``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from .bridge import AgentRunner

logger = logging.getLogger(__name__)


class HealthServer:
    """HTTP health check server.

    Exposes ``GET /health``:

    - 200 OK when every runner is connected (or there are no runners).
    - 503 Service Unavailable when any runner is disconnected.

    Response body lists each agent's connection status, useful when only
    a subset of agents are healthy.
    """

    def __init__(
        self,
        runners: list[AgentRunner],
        port: int = 8080,
        host: str = "0.0.0.0",
    ) -> None:
        self._runners = runners
        self._port = port
        self._host = host
        self._app = web.Application()
        self._app.router.add_get("/health", self._health_handler)
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        if self._runner is not None:
            return
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, self._host, self._port)
        await site.start()
        self._runner = runner
        logger.info("Health server listening on port %s", self._port)

    async def stop(self) -> None:
        if self._runner:
            try:
                await self._runner.cleanup()
            except Exception:
                logger.warning("Error during health server cleanup", exc_info=True)
            self._runner = None
            logger.info("Health server stopped")

    async def _health_handler(self, request: web.Request) -> web.Response:
        per_agent = [
            {"agent_id": r.agent_id, "connected": r.is_connected} for r in self._runners
        ]
        all_connected = all(a["connected"] for a in per_agent) if per_agent else True
        body: dict[str, Any] = {
            "status": "healthy" if all_connected else "unhealthy",
            "agents": per_agent,
            "agent_count": len(self._runners),
        }
        if not self._runners:
            body["warning"] = "no agents configured"
        return web.json_response(body, status=200 if all_connected else 503)
