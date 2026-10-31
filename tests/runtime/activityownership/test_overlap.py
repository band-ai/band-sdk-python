"""One tool ending never hides another tool still running in the same turn."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AsyncExitStack

import pytest

from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.toolactivity import end_activity, start_tool_activity, tool_activity
from band.runtime.tools import BandTool
from band.runtime.tools.agent import AgentTools
from tests.activitysupport import IDLE_BODY, REASONING_BODY, tool_body
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import ActivityPlatform, deliver_event

NATIVE_CALL_ID = "native-call"
NATIVE_TOOL = "Read"
BAND_TOOL = BandTool.GET_PARTICIPANTS


@pytest.mark.looptime
class TestOverlappingToolActivity:
    async def test_a_band_tool_ending_shows_the_native_tool_still_running(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            await activity_platform.shown(
                start_tool_activity(tools, NATIVE_CALL_ID, NATIVE_TOOL)
            )
            async with AsyncExitStack() as band_tool:
                await activity_platform.shown(
                    band_tool.enter_async_context(tool_activity(tools, BAND_TOOL))
                )
                await activity_platform.shown(band_tool.aclose())
            await activity_platform.shown(end_activity(tools, NATIVE_CALL_ID))

        ctx = execution(handler)
        await deliver_event(ctx, make_message_event())
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body(NATIVE_TOOL),
            tool_body(BAND_TOOL),
            tool_body(NATIVE_TOOL),
            REASONING_BODY,
            IDLE_BODY,
        ]

    async def test_a_native_tool_ending_keeps_the_band_tool_still_running(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            await activity_platform.shown(
                start_tool_activity(tools, NATIVE_CALL_ID, NATIVE_TOOL)
            )
            async with AsyncExitStack() as band_tool:
                await activity_platform.shown(
                    band_tool.enter_async_context(tool_activity(tools, BAND_TOOL))
                )
                await end_activity(tools, NATIVE_CALL_ID)
                # Nothing visible changed, so only the keep-alive reports.
                await activity_platform.next_body()
                await activity_platform.shown(band_tool.aclose())

        ctx = execution(handler)
        await deliver_event(ctx, make_message_event())
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body(NATIVE_TOOL),
            tool_body(BAND_TOOL),
            tool_body(BAND_TOOL),
            REASONING_BODY,
            IDLE_BODY,
        ]
