from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from unittest.mock import AsyncMock

import pytest

from band.platform.event import PlatformEvent
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.runtime.sessionconfig import SessionConfig
from band.runtime.tools.agent import AgentTools
from tests.runtime.activityownership.kit import ActivityPlatform
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    rest_client_over,
)


@pytest.fixture
def activity_platform() -> Iterator[ActivityPlatform]:
    platform = ActivityPlatform()
    yield platform
    assert platform.late_changes == [], "a visible change waited for a keep-alive"


@pytest.fixture
async def execution(
    activity_platform: ActivityPlatform,
) -> AsyncIterator[Callable[..., ExecutionContext]]:
    contexts: list[ExecutionContext] = []
    async with rest_client_over(activity_platform.respond) as rest:

        def create(
            handler: Callable[[ExecutionContext, PlatformEvent], Awaitable[None]],
            hub_room_id: str | None = None,
            **settings: object,
        ) -> ExecutionContext:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            runtime = AgentRuntime(
                link=link, agent_id=AGENT_ID, on_execute=handler, on_control=AsyncMock()
            )
            ctx = ExecutionContext(
                ROOM_ID,
                link,
                handler,
                agent_id=AGENT_ID,
                hub_room_id=hub_room_id,
                on_platform_stop=runtime._on_platform_stop,
                config=SessionConfig(enable_context_hydration=False, **settings),
            )
            contexts.append(ctx)
            return ctx

        yield create
        for ctx in contexts:
            activity_platform.activity_release.set()
            await ctx.stop()


@pytest.fixture
async def bare_tools(activity_platform: ActivityPlatform) -> AsyncIterator[AgentTools]:
    """Real tools built outside an execution, as adapters' unit tests build them."""
    async with rest_client_over(activity_platform.respond) as rest:
        yield AgentTools(ROOM_ID, rest)
