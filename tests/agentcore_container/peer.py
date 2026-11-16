"""Local Band identity peer that answers only once every expected caller is waiting."""

from __future__ import annotations

import asyncio

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tests.oneshot_server.kit import AGENT_ME, AGENT_ME_PATH

API_KEY_HEADER = "x-api-key"


class IdentityPeer:
    """Serves ``GET /api/v1/agent/me`` behind a barrier of ``callers`` requests.

    Holding every answer until all callers have asked proves their startups
    overlap: a lock serializing them would leave the barrier short forever.
    """

    def __init__(self, callers: int) -> None:
        self.api_keys: list[str] = []
        self._callers = callers
        self._all_waiting = asyncio.Event()
        self.app = Starlette(routes=[Route(AGENT_ME_PATH, self._agent_me)])

    async def _agent_me(self, request: Request) -> JSONResponse:
        self.api_keys.append(request.headers.get(API_KEY_HEADER, ""))
        if len(self.api_keys) == self._callers:
            self._all_waiting.set()
        await self._all_waiting.wait()
        return JSONResponse(AGENT_ME.model_dump(mode="json"))
