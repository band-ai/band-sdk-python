"""Shared helpers for tests/runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx

from band.client.rest import AsyncRestClient


@asynccontextmanager
async def rest_client_over(
    handler: Callable[[httpx.Request], httpx.Response],
) -> AsyncIterator[AsyncRestClient]:
    """A real ``band_rest.AsyncRestClient`` whose HTTP boundary is a fake
    transport instead of the network, so response-parsing errors (like the
    ``ValidationError`` -> ``ParsingError`` wrapping in
    ``raw_client.resolve_handle``) are raised by the real dependency rather
    than simulated.
    """
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as httpx_client:
        yield AsyncRestClient(api_key="test-key", httpx_client=httpx_client)
