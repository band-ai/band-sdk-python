"""A shared HTTP resource that stays open until the native work settles."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from band.integrations.uvicorn_server import (
    SERVER_START_TIMEOUT_S,
    SERVER_STOP_TIMEOUT_S,
    ManagedUvicornServer,
)


@asynccontextmanager
async def resource_peer() -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, str]]]:
    expected = {"value": uuid4().hex}

    async def read(request: Request) -> JSONResponse:
        return JSONResponse(expected)

    server = ManagedUvicornServer(
        Starlette(routes=[Route("/resource", read)]),
        host="127.0.0.1",
        port=0,
        start_timeout_s=SERVER_START_TIMEOUT_S,
        stop_timeout_s=SERVER_STOP_TIMEOUT_S,
    )
    await server.start()
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{server.bound_port}"
        ) as client:
            yield client, expected
    finally:
        await server.stop()
