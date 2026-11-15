"""Own external Anthropic transport resources."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2
import pytest
from anthropic import AsyncAnthropic

from tests.integrations.anthropic.peer import MessagesPeer


@pytest.fixture
async def anthropic_peer() -> AsyncIterator[tuple[MessagesPeer, AsyncAnthropic]]:
    peer = MessagesPeer()
    async with AsyncAnthropic(
        api_key="test",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(peer.answer)),
    ) as client:
        yield peer, client
