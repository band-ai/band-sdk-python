from __future__ import annotations

from collections.abc import Callable

from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from band.runtime.tools.types import BandTool
from band.runtime.working_state import end_tool_activity, report_tool_activity
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import ActivityPlatform


class TestBandToolActivity:
    async def test_direct_registered_method_and_model_event_are_observed_but_telemetry_is_quiet(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            await tools.get_participants()
            await report_tool_activity(tools, "Bash")
            await tools.send_event("private telemetry", "thought")
            assert activity_platform.activity_bodies[-1] == {
                "working": True,
                "activity": {"kind": "tool_call", "name": "Bash"},
            }
            await end_tool_activity(tools)
            await tools.execute_tool_call(
                BandTool.SEND_EVENT,
                {"content": "model telemetry", "message_type": "thought"},
            )

        ctx = execution(handler)
        await ctx._process_event(make_message_event())
        assert activity_platform.activity_bodies == [
            {"working": True, "activity": {"kind": "reasoning"}},
            {
                "working": True,
                "activity": {"kind": "tool_call", "name": BandTool.GET_PARTICIPANTS},
            },
            {"working": True, "activity": {"kind": "reasoning"}},
            {"working": True, "activity": {"kind": "tool_call", "name": "Bash"}},
            {"working": True, "activity": {"kind": "reasoning"}},
            {
                "working": True,
                "activity": {"kind": "tool_call", "name": BandTool.SEND_EVENT},
            },
            {"working": True, "activity": {"kind": "reasoning"}},
            {"working": False},
        ]
