"""Fresh foreground scopes report independently of a capped background resource."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable

import pytest

from band.core.protocols import AgentToolsProtocol
from band.core.turn_lifecycle import TaskRole, TurnTask, bind_turn_task, resource_tools
from band.platform.event import PlatformEvent
from band.runtime.activity import permission_wait
from band.runtime.execution import ExecutionContext
from band.runtime.toolactivity import start_native_tool_activity
from band.runtime.tools.agent import AgentTools
from tests.activitysupport import IDLE_BODY, tool_body
from tests.adapters.hold import Hold
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import ActivityPlatform, deliver_event
from tests.runtime.helpers import IDLE, KEEP_ALIVE

CAP_SECONDS = 4.0
BACKGROUND_TOOL = "Read"
FRESH_TOOL = "Bash"
FOLLOWUP_TOOL = "Write"


@pytest.mark.looptime
class TestIndependentOriginCaps:
    async def test_fresh_foregrounds_report_without_renewing_a_capped_resource(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        resource_entered, resource_release = asyncio.Event(), asyncio.Event()
        owners: list[TurnTask] = []
        background_tools: list[AgentToolsProtocol] = []
        first, second = Hold(), Hold()
        foregrounds = deque([(FRESH_TOOL, first), (FOLLOWUP_TOOL, second)])

        async def resource() -> None:
            resource_entered.set()
            await resource_release.wait()

        async def start_background(tools: AgentTools) -> None:
            owner = bind_turn_task(
                tools, asyncio.create_task(resource()), role=TaskRole.RESOURCE
            )
            owners.append(owner)
            background_tools.append(resource_tools(tools, owner))

        async def foreground(tools: AgentTools) -> None:
            name, held = foregrounds.popleft()
            await start_native_tool_activity(tools, name, name)
            await held.wait()
            await tools.send_notice(name, mentions=["alice"])

        handlers = deque([start_background, foreground, foreground])

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            await handlers.popleft()(AgentTools.from_context(ctx))

        ctx = execution(handler, max_working_state_seconds=CAP_SECONDS)
        deliveries: list[asyncio.Task[None]] = []
        try:
            async with asyncio.timeout(10 * CAP_SECONDS):
                await deliver_event(ctx, make_message_event(msg_id="old"))
                await resource_entered.wait()
                await activity_platform.shown(
                    start_native_tool_activity(
                        background_tools[0], "old", BACKGROUND_TOOL
                    )
                )

                await asyncio.sleep(CAP_SECONDS)
                await background_tools[0].send_notice("capped", mentions=["alice"])
                assert activity_platform.room_messages == ["capped"]
                assert not owners[0].task.done()

                fresh = asyncio.create_task(
                    deliver_event(ctx, make_message_event(msg_id="fresh"))
                )
                deliveries.append(fresh)
                await first.arrived()
                assert await activity_platform.body_matching(
                    tool_body(FRESH_TOOL)
                ) == tool_body(FRESH_TOOL)
                first.released.set()
                await fresh

                with permission_wait(background_tools[0]):
                    await start_native_tool_activity(
                        background_tools[0], "late", BACKGROUND_TOOL
                    )
                    followup = asyncio.create_task(
                        deliver_event(ctx, make_message_event(msg_id="followup"))
                    )
                    deliveries.append(followup)
                    await second.arrived()
                    assert await activity_platform.body_matching(
                        tool_body(FOLLOWUP_TOOL)
                    ) == tool_body(FOLLOWUP_TOOL)
                    assert activity_platform.reports_without_refreshes == [KEEP_ALIVE]
                    second.released.set()
                    await followup

                await background_tools[0].send_notice("surviving", mentions=["alice"])
                assert activity_platform.room_messages == [
                    "capped",
                    FRESH_TOOL,
                    FOLLOWUP_TOOL,
                    "surviving",
                ]
                assert activity_platform.marked("processed") == [
                    "old",
                    "fresh",
                    "followup",
                ]
                resource_release.set()
                await owners[0].task
                await ctx._join_activity_completion()
                assert activity_platform.activity_bodies[-1] == IDLE_BODY
                assert activity_platform.reports_without_refreshes == [KEEP_ALIVE, IDLE]
        finally:
            first.released.set()
            second.released.set()
            resource_release.set()
            await asyncio.gather(
                *deliveries, *(owner.task for owner in owners), return_exceptions=True
            )
            await ctx._join_activity_completion()
