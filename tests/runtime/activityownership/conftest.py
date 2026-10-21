from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

from band.platform.event import PlatformEvent
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.activityownership.kit import ActivityPlatform, IdleReportGate
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    rest_client_over,
)


@pytest.fixture
async def activity_platform() -> AsyncIterator[ActivityPlatform]:
    yield ActivityPlatform()


@pytest.fixture
def idle_report_gate(activity_platform: ActivityPlatform) -> IdleReportGate:
    return IdleReportGate(activity_platform)


@pytest.fixture
async def execution(
    idle_report_gate: IdleReportGate,
) -> AsyncIterator[Callable[..., ExecutionContext]]:
    contexts: list[ExecutionContext] = []
    async with rest_client_over(idle_report_gate.answer) as rest:

        def create(
            handler: Callable[[ExecutionContext, PlatformEvent], Awaitable[None]],
            *,
            on_message_control: Callable[
                [ExecutionContext, PlatformEvent], Awaitable[bool]
            ]
            | None = None,
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
                on_platform_stop=runtime._on_platform_stop,
                on_message_control=on_message_control,
                config=SessionConfig(enable_context_hydration=False, **settings),
            )
            contexts.append(ctx)
            return ctx

        yield create
        for ctx in contexts:
            await ctx.stop()
