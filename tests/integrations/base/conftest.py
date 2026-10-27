"""Real, unstarted execution contexts for participant notification tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from band.client.rest import aclose_rest_client
from band.platform.event import PlatformEvent
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext


async def reject_execution(ctx: ExecutionContext, event: PlatformEvent) -> None:
    raise AssertionError("Participant formatting must not execute a turn")


@pytest.fixture
async def context() -> AsyncIterator[ExecutionContext]:
    link = BandLink(
        agent_id="agent-1",
        api_key="test-key",
        rest_url="https://band.invalid",
        ws_url="wss://band.invalid",
    )
    try:
        yield ExecutionContext("room-1", link, reject_execution)
    finally:
        await aclose_rest_client(link.rest)


@pytest.fixture
def alice() -> dict[str, str]:
    return {"id": "user-1", "name": "Alice", "type": "User", "handle": "alice"}
