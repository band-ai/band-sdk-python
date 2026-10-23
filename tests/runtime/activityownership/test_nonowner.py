from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from band.core.turn_lifecycle import TurnTask, bind_turn_task
from band.platform.event import PlatformEvent
from band.runtime.activity import set_permission_wait
from band.runtime.execution import ExecutionContext
from band.runtime.toolactivity import report_tool_activity
from band.runtime.tools.agent import AgentTools
from tests.activitysupport import IDLE_BODY, REASONING_BODY, tool_body
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import (
    ActivityPlatform,
    deliver_event,
    past_a_keep_alive,
)


@pytest.mark.looptime
class TestNonOwnerObservations:
    async def test_a_busy_delivery_never_changes_the_owner_detail(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        release = asyncio.Event()
        workers: list[TurnTask] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            if workers:
                await report_tool_activity(tools, "unrelated")
                await set_permission_wait(tools, True)
                return
            await activity_platform.shown(report_tool_activity(tools, "Bash"))
            workers.append(bind_turn_task(tools, asyncio.create_task(release.wait())))

        ctx = execution(handler)
        await deliver_event(ctx, make_message_event(msg_id="owner"))
        await deliver_event(ctx, make_message_event(msg_id="busy"))
        await activity_platform.next_body()
        release.set()
        await workers[0].task
        await activity_platform.next_body()
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body("Bash"),
            tool_body("Bash"),
            IDLE_BODY,
        ]

    async def test_settled_turn_cannot_restart_reporting(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        captured: list[AgentTools] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            captured.append(AgentTools.from_context(ctx))

        ctx = execution(handler)
        await deliver_event(ctx, make_message_event())
        await ctx._join_activity_completion()
        before = list(activity_platform.activity_bodies)
        await report_tool_activity(captured[0], "Bash")
        await past_a_keep_alive()
        assert before[-1] == IDLE_BODY
        assert activity_platform.activity_bodies == before

    async def test_observation_from_a_waiting_delivery_never_starts_reporting(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        worker_release = asyncio.Event()
        waiting_tools: list[AgentTools] = []
        waiting_started = asyncio.Event()
        waiting_release = asyncio.Event()
        workers: list[TurnTask] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            if workers:
                waiting_tools.append(tools)
                waiting_started.set()
                await waiting_release.wait()
                return
            workers.append(
                bind_turn_task(tools, asyncio.create_task(worker_release.wait()))
            )

        ctx = execution(handler)
        await deliver_event(ctx, make_message_event(msg_id="owner"))
        waiting = asyncio.create_task(
            deliver_event(ctx, make_message_event(msg_id="waiting"))
        )
        await waiting_started.wait()
        worker_release.set()
        await workers[0].task
        await ctx._join_activity_completion()
        before = list(activity_platform.activity_bodies)
        await report_tool_activity(waiting_tools[0], "Bash")
        await past_a_keep_alive()
        assert before[-1] == IDLE_BODY
        assert activity_platform.activity_bodies == before
        waiting_release.set()
        await waiting
