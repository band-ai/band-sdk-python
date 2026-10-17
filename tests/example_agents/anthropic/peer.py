"""A real local JSON API for the example's two resource-backed tools."""

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
async def shared_api() -> AsyncIterator[
    tuple[httpx.AsyncClient, str, dict[str, dict[str, object]]]
]:
    values = {
        "todo": {"id": 1, "title": uuid4().hex, "userId": 1},
        "user": {"id": 1, "name": uuid4().hex},
    }

    async def read(request: Request) -> JSONResponse:
        resource = "todo" if request.path_params["resource"] == "todos" else "user"
        return JSONResponse(values[resource])

    server = ManagedUvicornServer(
        Starlette(routes=[Route("/{resource}/{id}", read)]),
        host="127.0.0.1",
        port=0,
        start_timeout_s=SERVER_START_TIMEOUT_S,
        stop_timeout_s=SERVER_STOP_TIMEOUT_S,
    )
    await server.start()
    url = f"http://127.0.0.1:{server.bound_port}"
    try:
        async with httpx.AsyncClient(base_url=url) as client:
            yield client, url, values
    finally:
        await server.stop()
