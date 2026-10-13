"""Fixtures for AgentRuntime control routing tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.testing.platform import platform_connection_stub
from tests.runtime.runtimecontrol.kit import (
    ControlledRooms,
    StoppingAdapter,
    never_executes,
)


@pytest.fixture
def link() -> BandLink:
    """A real link that is never connected: control routing needs no I/O."""
    platform = platform_connection_stub()
    return BandLink(
        agent_id=platform.agent_id,
        api_key=platform.api_key,
        ws_url=platform.ws_url,
        rest_url=platform.rest_url,
    )


@pytest.fixture
def rooms(link: BandLink) -> ControlledRooms:
    return ControlledRooms(link, "r1", "r2")


@pytest.fixture
def stopping_adapter() -> StoppingAdapter:
    return StoppingAdapter()


@pytest.fixture
async def stopping_room(
    stopping_adapter: StoppingAdapter,
) -> AsyncIterator[tuple[AgentRuntime, ExecutionContext]]:
    """One admitted room whose adapter hears every STOP notification."""
    link = MagicMock()
    link.get_next_message = AsyncMock(return_value=None)
    runtime = AgentRuntime(
        link=link,
        agent_id="agent-123",
        on_execute=never_executes,
        on_control=stopping_adapter.on_interrupt,
    )
    ctx = await runtime.rooms.admit("room-1")
    assert isinstance(ctx, ExecutionContext)
    try:
        yield runtime, ctx
    finally:
        stopping_adapter.release.set()
        await ctx.stop()
