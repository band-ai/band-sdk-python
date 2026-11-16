"""A real REST client whose HTTP the test's ``httpx_mock`` answers, and
logging restored around band-trigger's ``main()``, which configures it."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio

from band.client.rest import AsyncRestClient, aclose_rest_client
from tests.cli.triggerwire.kit import API_KEY, REST_URL
from tests.logsupport import restored_logging


@pytest_asyncio.fixture(loop_scope="function")
async def rest_client() -> AsyncIterator[AsyncRestClient]:
    client = AsyncRestClient(api_key=API_KEY, base_url=REST_URL.rstrip("/"))
    try:
        yield client
    finally:
        await aclose_rest_client(client)


@pytest.fixture
def cli_logging() -> Iterator[None]:
    with restored_logging():
        yield
