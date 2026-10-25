"""Real room tools connected to the existing platform HTTP peer."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from band.runtime.tools import AgentTools
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.runtime import ReplyingPlatform
from tests.runtime.helpers import ALICE, rest_client_over


@pytest.fixture
def native_startup_ready() -> None:
    """Exercise native readiness instead of the parent suite's startup stub."""


@pytest.fixture
def platform() -> ReplyingPlatform:
    return ReplyingPlatform()


@pytest.fixture
def participants_request() -> Iterator[Hold]:
    hold = Hold()
    try:
        yield hold
    finally:
        hold.released.set()


@pytest.fixture
async def tools(
    platform: ReplyingPlatform, participants_request: Hold
) -> AsyncIterator[AgentTools]:
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/participants"):
            await participants_request.wait()
        return platform.answer(request)

    async with rest_client_over(respond) as rest:
        yield AgentTools("room-123", rest, participants=[ALICE])
