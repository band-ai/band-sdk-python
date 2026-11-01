"""Background resources keep their original authority across foreground deliveries."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest

from band.core.turn_lifecycle import TaskRole, TurnTask, bind_turn_task
from band.platform.event import PlatformEvent
from band.runtime.activity import permission_wait
from band.runtime.execution import ExecutionContext
from band.runtime.toolactivity import start_native_tool_activity
from band.runtime.tools.agent import AgentTools
from tests.activitysupport import IDLE_BODY, REASONING_BODY, tool_body
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import ActivityPlatform, deliver_event


@pytest.mark.looptime
class TestBackgroundOrigins:
    async def test_old_response_stops_only_its_captured_background_origin(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        releases = [asyncio.Event(), asyncio.Event()]
        owners: list[TurnTask] = []
        tools: list[AgentTools] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            original = AgentTools.from_context(ctx)
            index = len(owners)
            owner = bind_turn_task(
                original,
                asyncio.create_task(releases[index].wait()),
                role=TaskRole.RESOURCE,
            )
            owners.append(owner)
            tools.append(original._for_resource(owner))

        ctx = execution(handler)
        try:
            await deliver_event(ctx, make_message_event(msg_id="old"))
            activity_platform.hold_next_activity = True
            activity_platform.activity_responses.append(
                httpx.Response(200, json={"data": {"working": False}})
            )
            await start_native_tool_activity(tools[0], "old", "Read")
            await activity_platform.activity_entered.wait()
            await deliver_event(ctx, make_message_event(msg_id="new"))
            await start_native_tool_activity(tools[1], "new", "Bash")
            activity_platform.activity_release.set()
            await asyncio.gather(owners[0].task, return_exceptions=True)
            assert owners[0].task.cancelled()
            assert not owners[1].task.done()
            assert await activity_platform.next_body() == tool_body("Bash")
            releases[1].set()
            await owners[1].task
            await ctx._join_activity_completion()
            assert activity_platform.activity_bodies[-1] == IDLE_BODY
            assert activity_platform.marked("processed") == ["old", "new"]
        finally:
            activity_platform.activity_release.set()
            for release in releases:
                release.set()
            await asyncio.gather(
                *(owner.task for owner in owners), return_exceptions=True
            )
            await ctx._join_activity_completion()

    async def test_killing_one_resource_preserves_sibling_effect_authority(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        owners: list[TurnTask] = []
        tools: list[AgentTools] = []
        release = asyncio.Event()

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            original = AgentTools.from_context(ctx)
            for _ in range(2):
                owner = bind_turn_task(
                    original,
                    asyncio.create_task(release.wait()),
                    role=TaskRole.RESOURCE,
                )
                owners.append(owner)
                tools.append(original._for_resource(owner))

        ctx = execution(handler)
        try:
            await deliver_event(ctx, make_message_event())
            owners[0].terminate()
            await asyncio.gather(owners[0].task, return_exceptions=True)
            with pytest.raises(asyncio.CancelledError):
                await tools[0].send_notice("stale", mentions=["alice"])
            await tools[1].send_notice("current", mentions=["alice"])
            await activity_platform.shown(
                start_native_tool_activity(tools[1], "live", "Read")
            )
            await start_native_tool_activity(tools[0], "stale", "Bash")
            assert activity_platform.room_messages == ["current"]
            assert ctx.interrupt()
            await asyncio.gather(owners[1].task, return_exceptions=True)
            await ctx._join_activity_completion()
            assert activity_platform.activity_bodies[-1] == IDLE_BODY
        finally:
            release.set()
            await asyncio.gather(
                *(owner.task for owner in owners), return_exceptions=True
            )
            await ctx._join_activity_completion()

    async def test_resource_revocation_removes_permission_before_its_cleanup_finishes(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        owners: list[TurnTask] = []
        tools: list[AgentTools] = []
        cleaning, release = asyncio.Event(), asyncio.Event()

        async def resource() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            original = AgentTools.from_context(ctx)
            for _ in range(2):
                owner = bind_turn_task(
                    original, asyncio.create_task(resource()), role=TaskRole.RESOURCE
                )
                owners.append(owner)
                tools.append(original._for_resource(owner))

        ctx = execution(handler)
        try:
            await deliver_event(ctx, make_message_event())
            with permission_wait(tools[0]):
                await activity_platform.next_body()
                owners[0].terminate()
                await cleaning.wait()
                assert await activity_platform.next_body() == REASONING_BODY
                assert not owners[0].task.done()
                await activity_platform.shown(
                    start_native_tool_activity(tools[1], "current", "Read")
                )
                release.set()
                await asyncio.gather(owners[0].task, return_exceptions=True)
            await activity_platform.shown(
                start_native_tool_activity(tools[1], "fresh", "Bash")
            )
            assert activity_platform.activity_bodies[-1] == tool_body("Bash")
            ctx.interrupt()
            await asyncio.gather(owners[1].task, return_exceptions=True)
            await ctx._join_activity_completion()
            assert activity_platform.activity_bodies[-1] == IDLE_BODY
        finally:
            release.set()
            for owner in owners:
                owner.terminate()
            await asyncio.gather(
                *(owner.task for owner in owners), return_exceptions=True
            )
            await ctx._join_activity_completion()
