from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from band.core.turn_lifecycle import TurnTask, bind_turn_task
from band.platform.event import PlatformEvent
from band.runtime.activity import report_memory_activity, report_tool_activity
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import (
    IDLE_BODY,
    REASONING_BODY,
    ActivityPlatform,
    tool_body,
)


@pytest.mark.looptime
class TestControlledTurnDetail:
    async def test_a_new_message_after_an_interrupt_shows_only_its_own_detail(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        cleanup_entered = asyncio.Event()
        cleanup_release = asyncio.Event()
        workers: list[TurnTask] = []

        async def bash(tools: AgentTools) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup_entered.set()
                await cleanup_release.wait()
                await report_memory_activity(tools)
                raise

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            if workers:
                cleanup_release.set()
                await asyncio.gather(workers[0].task, return_exceptions=True)
                await activity_platform.shown(report_tool_activity(tools, "Read"))
                return
            await activity_platform.shown(report_tool_activity(tools, "Bash"))
            workers.append(bind_turn_task(tools, asyncio.create_task(bash(tools))))

        ctx = execution(handler)
        await ctx._process_event(make_message_event(msg_id="interrupted"))
        assert ctx.interrupt()
        await cleanup_entered.wait()
        await ctx._process_event(make_message_event(msg_id="next"))
        assert workers[0].task.cancelled()
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body("Bash"),
            IDLE_BODY,
            REASONING_BODY,
            tool_body("Read"),
            IDLE_BODY,
        ]

    async def test_a_stop_echoed_on_a_detail_refresh_cancels_the_turn(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        captured: list[AgentTools] = []
        cancelled = asyncio.Event()

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            captured.append(tools)
            await activity_platform.shown(report_tool_activity(tools, "Bash"))
            activity_platform.stopped = True
            await report_tool_activity(tools, "Read")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await report_memory_activity(tools)
                cancelled.set()
                raise

        ctx = execution(handler)
        await ctx._process_event(make_message_event())
        assert cancelled.is_set()
        await report_tool_activity(captured[0], "Edit")
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body("Bash"),
            tool_body("Read"),
            IDLE_BODY,
        ]
