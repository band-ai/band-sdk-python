from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from pytest_httpx import HTTPXMock

from band.core.turn_lifecycle import bind_turn_task
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.conftest import make_participant_added_event
from tests.runtime.activityownership.kit import ActivityPlatform


class TestHousekeepingActivity:
    @pytest.mark.parametrize("detached", [False, True])
    async def test_registered_tools_and_workers_cannot_start_housekeeping_reports(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
        httpx_mock: HTTPXMock,
        detached: bool,
    ) -> None:
        handled, release = asyncio.Event(), asyncio.Event()

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            await tools.get_participants()
            if detached:
                bind_turn_task(tools, asyncio.create_task(release.wait()))
            handled.set()

        ctx = execution(handler)
        await ctx.start()
        try:
            await ctx.on_event(make_participant_added_event())
            await handled.wait()
            await ctx._wait_for_idle(timeout=2)
            assert not activity_platform.activity_bodies
            assert not httpx_mock.get_requests()
        finally:
            release.set()
