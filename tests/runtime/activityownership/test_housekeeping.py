from __future__ import annotations

import asyncio
from collections.abc import Callable

from band.core.turn_lifecycle import TurnTask, bind_turn_task
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.conftest import make_participant_added_event
from tests.runtime.helpers import LifecyclePlatform


class TestHousekeepingWorker:
    async def test_worker_bound_by_a_housekeeping_turn_never_reports_working(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: LifecyclePlatform,
    ) -> None:
        release = asyncio.Event()
        workers: list[TurnTask] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            worker = asyncio.create_task(release.wait())
            workers.append(bind_turn_task(AgentTools.from_context(ctx), worker))

        ctx = execution(handler)
        await ctx._process_event(make_participant_added_event())
        await ctx._join_activity_completion()
        assert not workers[0].task.done()
        assert activity_platform.activity_reports == []

        release.set()
        await workers[0].task
        await ctx._join_activity_completion()
        assert activity_platform.activity_reports == []
