from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from pytest_httpx import HTTPXMock

from band.core.turn_lifecycle import TurnTask, bind_turn_task
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from band.runtime.working_state import (
    end_tool_activity,
    report_memory_activity,
    report_tool_activity,
    set_permission_wait,
)
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import ActivityPlatform
from tests.runtime.helpers import ROOM_ID


class TestOwnedDetail:
    async def test_real_tools_observer_reports_canonical_wire_detail(
        self, execution: Callable[..., ExecutionContext], activity_platform: ActivityPlatform
    ) -> None:
        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            await report_tool_activity(tools, "Bash")
            await set_permission_wait(tools, True)
            await end_tool_activity(tools)
            await report_memory_activity(tools)
            await set_permission_wait(tools, False)
            await end_tool_activity(tools)

        ctx = execution(handler)
        await ctx._process_event(make_message_event())
        assert activity_platform.activity_bodies == [
            {"working": True, "activity": {"kind": "reasoning"}},
            {"working": True, "activity": {"kind": "tool_call", "name": "Bash"}},
            {"working": True, "activity": {"kind": "permission"}},
            {"working": True, "activity": {"kind": "memory"}},
            {"working": True, "activity": {"kind": "reasoning"}},
            {"working": False},
        ]

    @pytest.mark.looptime
    async def test_settled_owner_remains_authoritative_and_transient_tools_are_quiet(
        self, execution: Callable[..., ExecutionContext], activity_platform: ActivityPlatform
    ) -> None:
        release = asyncio.Event()
        captured: list[AgentTools] = []
        workers: list[TurnTask] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            if captured:
                await report_tool_activity(tools, "unrelated")
                return
            captured.append(tools)
            workers.append(bind_turn_task(tools, asyncio.create_task(release.wait())))
            await set_permission_wait(tools, True)

        ctx = execution(handler)
        await ctx._process_event(make_message_event(msg_id="original"))
        await ctx._process_event(make_message_event(msg_id="busy"))
        await asyncio.sleep(6.1)
        assert (
            activity_platform.activity_bodies[-2:]
            == [{"working": True, "activity": {"kind": "permission"}}] * 2
        )
        await report_tool_activity(captured[0], "Bash")
        await set_permission_wait(captured[0], False)
        assert activity_platform.activity_bodies[-1] == {
            "working": True,
            "activity": {"kind": "tool_call", "name": "Bash"},
        }
        release.set()
        await workers[0].task
        await asyncio.sleep(0)
        await ctx._join_activity_completion()
        assert activity_platform.activity_bodies[-1] == {"working": False}
        before = list(activity_platform.activity_bodies)
        await end_tool_activity(captured[0])
        assert activity_platform.activity_bodies == before

    async def test_queued_observation_is_rechecked_after_stop_and_replacement(
        self, execution: Callable[..., ExecutionContext], activity_platform: ActivityPlatform
    ) -> None:
        captured: list[AgentTools] = []
        release = asyncio.Event()

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            captured.append(AgentTools.from_context(ctx))
            await release.wait()

        ctx = execution(handler)
        turn = asyncio.create_task(ctx._process_event(make_message_event(msg_id="old")))
        while not captured:
            await asyncio.sleep(0)
        activity_platform.hold_next_activity = True
        change = asyncio.create_task(report_tool_activity(captured[0], "Bash"))
        await activity_platform.activity_entered.wait()
        stale = asyncio.create_task(report_memory_activity(captured[0]))
        ctx.interrupt()
        activity_platform.activity_release.set()
        await asyncio.gather(turn, change, stale, return_exceptions=True)
        release.set()
        await ctx._process_event(make_message_event(msg_id="new"))
        assert activity_platform.activity_bodies[-3:] == [
            {"working": False},
            {"working": True, "activity": {"kind": "reasoning"}},
            {"working": False},
        ]
        assert not any(
            body.get("activity", {}).get("kind") == "memory"
            for body in activity_platform.activity_bodies
        )

    @pytest.mark.parametrize("excluded", ["disabled", "hub"])
    async def test_excluded_reporting_ignores_observations_without_discovery(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
        httpx_mock: HTTPXMock,
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
        assert not httpx_mock.get_requests()
