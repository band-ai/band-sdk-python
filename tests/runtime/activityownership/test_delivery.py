from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.toolactivity import report_tool_activity
from band.runtime.tools.agent import AgentTools
from tests.activitysupport import tool_body
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import ActivityPlatform, deliver_event


@pytest.mark.looptime
class TestNonBlockingDelivery:
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
        await deliver_event(ctx, make_message_event())
        assert observed_before_delivery == [True]
        assert seconds_until_sent == [0]
        assert activity_platform.activity_bodies[1] == tool_body("Bash")
