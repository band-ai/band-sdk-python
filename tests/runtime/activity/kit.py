"""Activity response peer and managed execution."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx

from band.platform.event import PlatformEvent
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    LifecyclePlatform,
    rest_client_over,
)


class ActivityPeer(LifecyclePlatform):
    """Hold an activity response while controls or the turn's lifetime advance."""

    def __init__(self) -> None:
        super().__init__()
        self.hold = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def respond(self, request: httpx.Request) -> httpx.Response:
        response = self.answer(request)
        if self.hold and request.url.path.endswith("/activity"):
            self.hold = False
            self.entered.set()
            await self.release.wait()
        return response


@asynccontextmanager
async def activity_context(
    peer: ActivityPeer,
    handler: Callable[[ExecutionContext, PlatformEvent], Awaitable[None]],
    observer: AsyncMock,
    *,
    enabled: bool = True,
    max_cycle_seconds: float | None = None,
) -> AsyncIterator[ExecutionContext]:
    async with rest_client_over(peer.respond) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        runtime = AgentRuntime(
            link=link, agent_id=AGENT_ID, on_execute=handler, on_control=observer
        )
        ctx = ExecutionContext(
            ROOM_ID,
            link,
            handler,
            agent_id=AGENT_ID,
            on_platform_stop=runtime._on_platform_stop,
            config=SessionConfig(
                enable_context_hydration=False,
                enable_working_state=enabled,
                max_cycle_seconds=max_cycle_seconds,
            ),
        )
        try:
            yield ctx
        finally:
            peer.release.set()
            await ctx.stop()
