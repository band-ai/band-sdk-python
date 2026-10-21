from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from band.core.turn_lifecycle import TurnTask, bind_turn_task
from band.platform.event import PlatformEvent
from band.runtime.activity import (
    end_tool_activity,
    report_memory_activity,
    report_tool_activity,
    set_permission_wait,
)
from band.runtime.execution import ExecutionContext
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
    ObservedIdle,
    past_a_keep_alive,
)
from tests.runtime.helpers import ROOM_ID


class TestOwnedDetail:
    @pytest.mark.looptime
    async def test_a_turn_through_a_permission_wait_reports_each_visible_step(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            await activity_platform.shown(report_tool_activity(tools, "Bash"))
            await activity_platform.shown(end_tool_activity(tools))
            await activity_platform.shown(set_permission_wait(tools, True))
            # Hidden behind the wait: the room keeps seeing permission.
            await report_tool_activity(tools, "Edit")
            await activity_platform.shown(set_permission_wait(tools, False))
            await activity_platform.shown(report_memory_activity(tools))
            await tools.send_message("Done", mentions=["alice"])

        ctx = execution(handler)
        await ctx._process_event(make_message_event())
        assert activity_platform.posts == ["messages"]
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body("Bash"),
            REASONING_BODY,
            PERMISSION_BODY,
            tool_body("Edit"),
            MEMORY_BODY,
            IDLE_BODY,
        ]

    @pytest.mark.looptime
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
            await activity_platform.shown(report_tool_activity(tools, "Bash"))
            workers.append(bind_turn_task(tools, asyncio.create_task(release.wait())))

        ctx = execution(handler)
        await ctx._process_event(make_message_event())
        owner = captured[0]
        await activity_platform.shown(set_permission_wait(owner, True))
        await activity_platform.bodies_reach(len(activity_platform.activity_bodies) + 1)
        # Hidden behind the wait: the room keeps seeing permission.
        await report_tool_activity(owner, "Edit")
        await activity_platform.shown(set_permission_wait(owner, False))
        release.set()
        await workers[0].task
        await activity_platform.bodies_reach(len(activity_platform.activity_bodies) + 1)
        await ctx._join_activity_completion()
        await end_tool_activity(owner)
        await past_a_keep_alive()
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body("Bash"),
            PERMISSION_BODY,
            PERMISSION_BODY,
            tool_body("Edit"),
            IDLE_BODY,
        ]

    @pytest.mark.looptime
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
        await ctx._process_event(make_message_event(msg_id="owner"))
        await ctx._process_event(make_message_event(msg_id="busy"))
        await activity_platform.bodies_reach(len(activity_platform.activity_bodies) + 1)
        release.set()
        await workers[0].task
        await activity_platform.bodies_reach(len(activity_platform.activity_bodies) + 1)
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body("Bash"),
            tool_body("Bash"),
            IDLE_BODY,
        ]

    @pytest.mark.looptime
    async def test_settled_turn_cannot_restart_reporting(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        captured: list[AgentTools] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            captured.append(AgentTools.from_context(ctx))

        ctx = execution(handler)
        await ctx._process_event(make_message_event())
        await ctx._join_activity_completion()
        before = list(activity_platform.activity_bodies)
        await report_tool_activity(captured[0], "Bash")
        await past_a_keep_alive()
        assert before[-1] == IDLE_BODY
        assert activity_platform.activity_bodies == before

    @pytest.mark.looptime
    async def test_graceful_stop_keeps_the_draining_turn_detail_current(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        captured: list[AgentTools] = []
        handler_started = asyncio.Event()
        release = asyncio.Event()

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            captured.append(AgentTools.from_context(ctx))
            handler_started.set()
            await release.wait()

        ctx = execution(handler)
        idle = ctx._idle_event = ObservedIdle()
        await ctx.start()
        await ctx.on_event(make_message_event())
        await handler_started.wait()
        stopping = asyncio.create_task(ctx.stop(timeout=30))
        await idle.awaited.wait()
        await activity_platform.shown(report_tool_activity(captured[0], "Bash"))
        release.set()
        assert await stopping
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body("Bash"),
            IDLE_BODY,
        ]

    @pytest.mark.looptime
    async def test_an_interrupted_turn_cannot_change_detail_while_unwinding(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        cleanup_entered = asyncio.Event()
        cleanup_release = asyncio.Event()

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            await activity_platform.shown(report_tool_activity(tools, "Bash"))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await report_memory_activity(tools)
                cleanup_entered.set()
                await cleanup_release.wait()
                raise

        ctx = execution(handler)
        turn = asyncio.create_task(ctx._process_event(make_message_event()))
        await activity_platform.bodies_reach(2)
        ctx.interrupt()
        await cleanup_entered.wait()
        # Less than the cleanup grace and the keep-alive: only a change could post.
        await asyncio.sleep(0.5)
        cleanup_release.set()
        await turn
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body("Bash"),
            IDLE_BODY,
        ]

    @pytest.mark.looptime
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
        await ctx._process_event(make_message_event(msg_id="owner"))
        waiting = asyncio.create_task(
            ctx._process_event(make_message_event(msg_id="waiting"))
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

    @pytest.mark.looptime
    async def test_a_new_turn_starts_at_reasoning(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        turns: list[ExecutionContext] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            turns.append(ctx)
            if len(turns) > 1:
                return
            tools = AgentTools.from_context(ctx)
            await activity_platform.shown(report_tool_activity(tools, "Bash"))
            await activity_platform.shown(set_permission_wait(tools, True))

        ctx = execution(handler)
        await ctx._process_event(make_message_event(msg_id="first"))
        await ctx._process_event(make_message_event(msg_id="second"))
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body("Bash"),
            PERMISSION_BODY,
            IDLE_BODY,
            REASONING_BODY,
            IDLE_BODY,
        ]

    @pytest.mark.looptime
    async def test_a_tool_never_waits_for_the_platform(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        observed_before_delivery: list[bool] = []
        seconds_until_sent: list[float] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            loop = asyncio.get_running_loop()
            activity_platform.hold_next_activity = True
            observed_at = loop.time()
            observing = asyncio.create_task(report_tool_activity(tools, "Bash"))
            await activity_platform.activity_entered.wait()
            seconds_until_sent.append(loop.time() - observed_at)
            observed_before_delivery.append(observing.done())
            activity_platform.activity_release.set()

        ctx = execution(handler)
        await ctx._process_event(make_message_event())
        assert observed_before_delivery == [True]
        assert seconds_until_sent == [0]
        assert activity_platform.activity_bodies[1] == tool_body("Bash")

    @pytest.mark.parametrize("excluded", ["disabled", "hub"])
    async def test_excluded_reporting_ignores_observations(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
        excluded: str,
    ) -> None:
        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            await report_tool_activity(tools, "Bash")
            await set_permission_wait(tools, True)
            await report_memory_activity(tools)
            await end_tool_activity(tools)

        settings = (
            {"enable_working_state": False}
            if excluded == "disabled"
            else {"hub_room_id": ROOM_ID}
        )
        ctx = execution(handler, **settings)
        await ctx._process_event(make_message_event())
        assert not activity_platform.activity_bodies
