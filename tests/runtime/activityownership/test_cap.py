from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from band.platform.event import PlatformEvent
from band.runtime.activity import set_permission_wait
from band.runtime.execution import ExecutionContext
from band.runtime.toolactivity import report_tool_activity
from band.runtime.tools.agent import AgentTools
from tests.activitysupport import IDLE_BODY, REASONING_BODY, tool_body
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import ActivityPlatform, deliver_event

CAP_SECONDS = 10.0
# Keep-alives at 3, 6 and 9 s; the one due at 12 s finds the cap passed.
LONG_TOOL_SECONDS = 15.0


@pytest.mark.looptime
class TestDetailCap:
    async def test_no_detail_is_reported_past_the_cap_until_the_turn_ends(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            await activity_platform.shown(report_tool_activity(tools, "Bash"))
            await asyncio.sleep(LONG_TOOL_SECONDS)
            await report_tool_activity(tools, "Edit")
            await set_permission_wait(tools, True)
            await set_permission_wait(tools, False)

        ctx = execution(handler, max_working_state_seconds=CAP_SECONDS)
        await deliver_event(ctx, make_message_event())
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            *[tool_body("Bash")] * 4,
            IDLE_BODY,
        ]
