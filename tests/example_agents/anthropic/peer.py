"""A real local JSON API for the example's two resource-backed tools."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tests.adaptercleanup import resource_peer


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

    async with resource_peer([Route("/{resource}/{id}", read)]) as client:
        yield client, str(client.base_url).rstrip("/"), values
