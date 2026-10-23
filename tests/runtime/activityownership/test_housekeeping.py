from __future__ import annotations

import asyncio
from collections.abc import Callable

from band.core.turn_lifecycle import TurnTask, bind_turn_task
from band.platform.event import MessageEvent, PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.conftest import make_message_event, make_participant_added_event
from tests.runtime.activityownership.kit import deliver_event
from tests.runtime.helpers import IDLE, KEEP_ALIVE, LifecyclePlatform


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
        await deliver_event(ctx, make_participant_added_event())
        await ctx._join_activity_completion()
        assert not workers[0].task.done()
        assert activity_platform.activity_reports == []

        release.set()
        await workers[0].task
        await ctx._join_activity_completion()
        assert activity_platform.activity_reports == []

    async def test_housekeeping_worker_leaves_message_owners_indicator_alone(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: LifecyclePlatform,
    ) -> None:
        releases = {"message": asyncio.Event(), "housekeeping": asyncio.Event()}
        workers: dict[str, TurnTask] = {}

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            kind = "message" if isinstance(event, MessageEvent) else "housekeeping"
            worker = asyncio.create_task(releases[kind].wait())
            workers[kind] = bind_turn_task(AgentTools.from_context(ctx), worker)

        ctx = execution(handler)
        await deliver_event(ctx, make_message_event())
        await deliver_event(ctx, make_participant_added_event())
        await ctx._join_activity_completion()
        assert activity_platform.activity_reports == [KEEP_ALIVE]

        releases["housekeeping"].set()
        await workers["housekeeping"].task
        await asyncio.sleep(0)
        await ctx._join_activity_completion()
        assert activity_platform.activity_reports == [KEEP_ALIVE]

        releases["message"].set()
        await workers["message"].task
        await asyncio.sleep(0)
        await ctx._join_activity_completion()
        assert activity_platform.activity_reports == [KEEP_ALIVE, IDLE]
