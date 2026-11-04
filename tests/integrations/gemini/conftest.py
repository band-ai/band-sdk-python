"""Own external Gemini transport resources."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from google import genai
from google.genai import types

from tests.integrations.gemini.peer import GeneratePeer


@pytest.fixture
async def gemini_peer() -> AsyncIterator[tuple[GeneratePeer, genai.Client]]:
    peer = GeneratePeer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.answer)) as http:
        client = genai.Client(
            api_key="test", http_options=types.HttpOptions(httpx_async_client=http)
        )
        try:
            yield peer, client
        finally:
            await client.aio.aclose()
            client.close()
