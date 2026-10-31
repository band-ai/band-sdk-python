"""ACP running-state translation reaches the activity HTTP boundary without titles."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from acp.helpers import start_tool_call, update_tool_call

from band.integrations.acp.types import ToolStatus
from band.platform.event import PlatformEvent
from band.runtime.activity import UNKNOWN_ACTIVITY_TOOL_NAME
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from band.runtime.tools.types import BandTool
from tests.activitysupport import IDLE_BODY, REASONING_BODY, tool_body
from tests.conftest import make_message_event
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.runtime.activityownership.kit import ActivityPlatform, deliver_event


@pytest.mark.looptime
class TestACPActivity:
    async def test_pending_call_does_not_hide_running_work_or_expose_its_title(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        agent = FakeACPAgent()

        @agent.on_prompt
        async def turn(peer: FakeACPAgent, sid: str) -> None:
            await peer.emit(
                sid,
                start_tool_call(
                    "native",
                    "Read /private/sentinel-token.txt",
                    status=ToolStatus.IN_PROGRESS,
                ),
            )
            assert await activity_platform.body_at(1) == tool_body(
                UNKNOWN_ACTIVITY_TOOL_NAME
            )
            await peer.emit(
                sid,
                start_tool_call(
                    "pending",
                    "private second title",
                    status=ToolStatus.PENDING,
                    raw_input={
                        "server": "band",
                        "tool": BandTool.GET_PARTICIPANTS,
                        "arguments": {},
                    },
                ),
            )
            await peer.emit(
                sid, update_tool_call("native", status=ToolStatus.COMPLETED)
            )
            assert await activity_platform.body_at(2) == REASONING_BODY
            await peer.emit(
                sid, update_tool_call("pending", status=ToolStatus.IN_PROGRESS)
            )
            assert await activity_platform.body_at(3) == tool_body(
                BandTool.GET_PARTICIPANTS
            )
            await peer.emit(
                sid, update_tool_call("pending", status=ToolStatus.COMPLETED)
            )
            assert await activity_platform.body_at(4) == REASONING_BODY

        async with acp_adapter(
            agent, fake_agent_config(inject_band_tools=True), emit=()
        ) as session:

            async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
                await session.send("work", tools=AgentTools.from_context(ctx))

            ctx = execution(handler)
            await deliver_event(ctx, make_message_event())
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body(UNKNOWN_ACTIVITY_TOOL_NAME),
            REASONING_BODY,
            tool_body(BandTool.GET_PARTICIPANTS),
            REASONING_BODY,
            IDLE_BODY,
        ]
