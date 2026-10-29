from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import ExitStack
from functools import partial

import pytest

from band.core.turn_lifecycle import TurnTask, bind_turn_task
from band.platform.event import PlatformEvent
from band.runtime.activity import permission_wait
from band.runtime.execution import ExecutionContext
from band.runtime.toolactivity import (
    end_activity,
    start_memory_activity,
    start_tool_activity,
)
from band.runtime.tools.agent import AgentTools
from tests.activitysupport import (
    IDLE_BODY,
    MEMORY_BODY,
    PERMISSION_BODY,
    REASONING_BODY,
    tool_body,
)
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import (
    ActivityPlatform,
    deliver_event,
    past_a_keep_alive,
)


@pytest.mark.looptime
class TestOwnedDetail:
    async def test_a_turn_through_a_permission_wait_reports_each_visible_step(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            await activity_platform.shown(start_tool_activity(tools, "bash", "Bash"))
            await activity_platform.shown(end_activity(tools, "bash"))
            with ExitStack() as waits:
                await activity_platform.shown(
                    partial(waits.enter_context, permission_wait(tools))
                )
                # Hidden behind the wait: the room keeps seeing permission.
                await start_tool_activity(tools, "edit", "Edit")
                await activity_platform.shown(waits.close)
            await activity_platform.shown(start_memory_activity(tools, "memory"))
            await activity_platform.shown(end_activity(tools, "memory"))
            await activity_platform.shown(end_activity(tools, "edit"))
            await tools.send_message("Done", mentions=["alice"])

        ctx = execution(handler)
        await deliver_event(ctx, make_message_event())
        assert activity_platform.posts == ["messages"]
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body("Bash"),
            REASONING_BODY,
            PERMISSION_BODY,
            tool_body("Edit"),
            MEMORY_BODY,
            # Memory ending reveals the tool still running.
            tool_body("Edit"),
            REASONING_BODY,
            # The reply is a Band tool call too; it ends back at reasoning.
            REASONING_BODY,
            IDLE_BODY,
        ]

    async def test_a_settled_owner_keeps_reporting_its_worker_detail(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        release = asyncio.Event()
        captured: list[AgentTools] = []
        workers: list[TurnTask] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            captured.append(tools)
            await activity_platform.shown(start_tool_activity(tools, "bash", "Bash"))
            workers.append(bind_turn_task(tools, asyncio.create_task(release.wait())))

        ctx = execution(handler)
        await deliver_event(ctx, make_message_event())
        owner = captured[0]
        with ExitStack() as waits:
            await activity_platform.shown(
                partial(waits.enter_context, permission_wait(owner))
            )
            # The keep-alive repeats PERMISSION while the wait holds.
            await activity_platform.next_body()
            # Hidden behind the wait: the room keeps seeing permission.
            await start_tool_activity(owner, "edit", "Edit")
            await activity_platform.shown(waits.close)
        release.set()
        await workers[0].task
        await activity_platform.next_body()
        await ctx._join_activity_completion()
        await end_activity(owner, "edit")
        await past_a_keep_alive()
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body("Bash"),
            PERMISSION_BODY,
            PERMISSION_BODY,
            tool_body("Edit"),
            IDLE_BODY,
        ]

    async def test_a_new_turn_starts_at_reasoning(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        turns: list[ExecutionContext] = []
        with ExitStack() as waits:

            async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
                turns.append(ctx)
                if len(turns) > 1:
                    return
                tools = AgentTools.from_context(ctx)
                await activity_platform.shown(
                    start_tool_activity(tools, "bash", "Bash")
                )
                await activity_platform.shown(
                    partial(waits.enter_context, permission_wait(tools))
                )

            ctx = execution(handler)
            await deliver_event(ctx, make_message_event(msg_id="first"))
            await deliver_event(ctx, make_message_event(msg_id="second"))
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body("Bash"),
            PERMISSION_BODY,
            IDLE_BODY,
            REASONING_BODY,
            IDLE_BODY,
        ]
