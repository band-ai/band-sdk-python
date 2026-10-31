"""A permission wait preserves activity from an overlapping custom tool."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.runtime.activity import permission_wait
from band.runtime.custom_tools import execute_custom_tool
from band.runtime.execution import ExecutionContext
from band.runtime.toolactivity import end_activity, start_tool_activity
from tests.activitysupport import PERMISSION_BODY, REASONING_BODY, tool_body
from tests.conftest import make_message_event
from tests.integrations.activitykit import LookupInput
from tests.runtime.permissionactivity.kit import HeldTurn, RoomActivityPlatform
from tests.runtime.scheduler.driver import attempt_delivery


@pytest.mark.looptime
class TestPermissionToolOverlap:
    async def test_finishing_native_work_during_a_wait_preserves_the_custom_tool(
        self,
        held_room: Callable[[str], tuple[ExecutionContext, HeldTurn]],
        permission_platform: RoomActivityPlatform,
        gated_lookup: tuple[
            Callable[[LookupInput], Awaitable[str]], asyncio.Event, asyncio.Event
        ],
    ) -> None:
        ctx, turn = held_room("overlapping-activity")
        await attempt_delivery(ctx, make_message_event(room_id=ctx.room_id))
        tools = turn.tools[0]
        lookup, entered, release = gated_lookup

        await start_tool_activity(tools, "native", "Read")
        custom = asyncio.create_task(
            execute_custom_tool((LookupInput, lookup), {"note": "done"}, tools=tools)
        )
        try:
            await entered.wait()
            assert await permission_platform.checkpoint(ctx) == tool_body("lookup")
            with permission_wait(tools):
                assert await permission_platform.checkpoint(ctx) == PERMISSION_BODY
                await end_activity(tools, "native")
                assert await permission_platform.checkpoint(ctx) == PERMISSION_BODY
            assert await permission_platform.checkpoint(ctx) == tool_body("lookup")
            release.set()
            assert await custom == "done"
            assert await permission_platform.checkpoint(ctx) == REASONING_BODY
        finally:
            release.set()
            await asyncio.gather(custom, return_exceptions=True)

    async def test_cancelling_custom_work_during_a_wait_preserves_native_activity(
        self,
        held_room: Callable[[str], tuple[ExecutionContext, HeldTurn]],
        permission_platform: RoomActivityPlatform,
        gated_lookup: tuple[
            Callable[[LookupInput], Awaitable[str]], asyncio.Event, asyncio.Event
        ],
    ) -> None:
        ctx, turn = held_room("cancelled-custom-activity")
        await attempt_delivery(ctx, make_message_event(room_id=ctx.room_id))
        tools = turn.tools[0]
        lookup, entered, release = gated_lookup

        await start_tool_activity(tools, "native", "Read")
        custom = asyncio.create_task(
            execute_custom_tool((LookupInput, lookup), {"note": "done"}, tools=tools)
        )
        try:
            await entered.wait()
            with permission_wait(tools):
                assert await permission_platform.checkpoint(ctx) == PERMISSION_BODY
                custom.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await custom
                assert await permission_platform.checkpoint(ctx) == PERMISSION_BODY
            assert await permission_platform.checkpoint(ctx) == tool_body("Read")
            await end_activity(tools, "native")
            assert await permission_platform.checkpoint(ctx) == REASONING_BODY
        finally:
            release.set()
            await asyncio.gather(custom, return_exceptions=True)
