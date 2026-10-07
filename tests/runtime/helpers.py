"""Shared helpers for tests/runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx

from band.client.rest import AsyncRestClient
from band.core.memory_types import (
    MemorySegment,
    MemoryStoreScope,
    MemorySystem,
    MemoryType,
)
from tests.identifiers import UUID_ID


@asynccontextmanager
async def rest_client_over(
    handler: Callable[[httpx.Request], httpx.Response],
) -> AsyncIterator[AsyncRestClient]:
    """A real ``AsyncRestClient`` whose HTTP boundary is ``handler`` instead
    of the network, so request building and response parsing run through the
    real dependency rather than being simulated."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as httpx_client:
        yield AsyncRestClient(api_key="test-key", httpx_client=httpx_client)


@asynccontextmanager
async def memory_client() -> AsyncIterator[tuple[AsyncRestClient, list[httpx.Request]]]:
    """Record real memory requests and return an item both surfaces can parse."""
    requests: list[httpx.Request] = []
    item = {
        "id": UUID_ID,
        "content": "remember this",
        "system": MemorySystem.WORKING,
        "type": MemoryType.SEMANTIC,
        "segment": MemorySegment.USER,
        "scope": MemoryStoreScope.AGENT,
        "inserted_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": item})

    async with rest_client_over(answer) as rest:
        yield rest, requests
