"""A real REST client whose HTTP the test's ``httpx_mock`` answers."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from band.client.rest import AsyncRestClient, aclose_rest_client
from tests.cli.triggerwire.kit import API_KEY, REST_URL


@pytest_asyncio.fixture(loop_scope="function")
async def rest_client() -> AsyncIterator[AsyncRestClient]:
    client = AsyncRestClient(api_key=API_KEY, base_url=REST_URL.rstrip("/"))
    try:
        yield client
    finally:
        await aclose_rest_client(client)
