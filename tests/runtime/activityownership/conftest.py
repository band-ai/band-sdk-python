from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from unittest.mock import AsyncMock

import pytest
from pytest_httpx import HTTPXMock

import band.platform.message_lifecycle as lifecycle
from band.platform.activity import ActivitySupport
from band.platform.event import PlatformEvent
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.activityownership.kit import ActivityPlatform
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    rest_client_over,
)


@pytest.fixture
async def activity_platform(
    monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> AsyncIterator[ActivityPlatform]:
    monkeypatch.setattr(lifecycle, "activity_support", ActivitySupport())
    httpx_mock.add_response(
        url="https://app.band.ai/api/version",
        json={"agent_version": "1.25.0"},
        is_optional=True,
    )
    yield ActivityPlatform()


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
