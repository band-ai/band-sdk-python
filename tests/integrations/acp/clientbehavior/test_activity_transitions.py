"""ACP partial updates describe running operations without activating pending calls."""

from __future__ import annotations

import pytest
from acp.helpers import start_tool_call, update_tool_call
from acp.schema import ToolCallProgress, ToolCallStart

from band.core.types import Emit
from band.integrations.acp.types import ToolStatus
from band.runtime.activity import UNKNOWN_ACTIVITY_TOOL_NAME
from band.runtime.tools.types import BandTool
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.acp_toolkit.agent import cursor_mcp_naming
from tests.integrations.acp.clientbehavior.kit import band_activity_call, observed_tools

FIRST = BandTool.GET_PARTICIPANTS
SECOND = BandTool.LOOKUP_PEERS


class TestActivityTransitions:
    @pytest.mark.parametrize(
        "emit", [(), (Emit.TOOL_CALLS,)], ids=["silent", "narrated"]
    )
    @pytest.mark.parametrize(
        ("updates", "expected"),
        [
            (
                [
                    band_activity_call("a", FIRST, ToolStatus.IN_PROGRESS),
                    band_activity_call("b", SECOND, ToolStatus.PENDING),
                    update_tool_call("a", status=ToolStatus.COMPLETED),
                    update_tool_call("b", status=ToolStatus.IN_PROGRESS),
                    update_tool_call("b", status=ToolStatus.COMPLETED),
                ],
                [
                    shown_tool(FIRST),
                    SHOWN_REASONING,
                    shown_tool(SECOND),
                    SHOWN_REASONING,
                ],
            ),
            (
                [
                    band_activity_call("a", FIRST, None),
                    update_tool_call("a", status=ToolStatus.IN_PROGRESS),
                    update_tool_call("a", title="another private title"),
                    update_tool_call("a", status=ToolStatus.COMPLETED),
                ],
                [shown_tool(FIRST), SHOWN_REASONING],
            ),
            (
                [
                    band_activity_call("a", FIRST, ToolStatus.COMPLETED),
                    update_tool_call("a", status=ToolStatus.IN_PROGRESS),
                ],
                [],
            ),
            (
                [
                    band_activity_call("a", FIRST, ToolStatus.IN_PROGRESS),
                    band_activity_call("b", SECOND, ToolStatus.IN_PROGRESS),
                    update_tool_call("a", status=ToolStatus.PENDING),
                    update_tool_call("a", status=ToolStatus.IN_PROGRESS),
                    update_tool_call("a", status=ToolStatus.COMPLETED),
                    update_tool_call("b", status=ToolStatus.FAILED),
                ],
                [
                    shown_tool(FIRST),
                    shown_tool(SECOND),
                    shown_tool(FIRST),
                    shown_tool(SECOND),
                    SHOWN_REASONING,
                ],
            ),
            (
                [
                    update_tool_call("a", status=ToolStatus.IN_PROGRESS),
                    update_tool_call("a", status=ToolStatus.COMPLETED),
                ],
                [shown_tool(UNKNOWN_ACTIVITY_TOOL_NAME), SHOWN_REASONING],
            ),
            (
                [
                    start_tool_call("a", "private", status=ToolStatus.IN_PROGRESS),
                    band_activity_call("b", SECOND, ToolStatus.IN_PROGRESS),
                    cursor_mcp_naming("a", "band", FIRST, {}),
                    update_tool_call("b", status=ToolStatus.COMPLETED),
                    update_tool_call("a", status=ToolStatus.COMPLETED),
                ],
                [
                    shown_tool(UNKNOWN_ACTIVITY_TOOL_NAME),
                    shown_tool(SECOND),
                    shown_tool(FIRST),
                    SHOWN_REASONING,
                ],
            ),
        ],
        ids=[
            "pending-does-not-hide-running",
            "partial-update-retains-running",
            "terminal-does-not-resurrect",
            "resume-starts-again",
            "update-without-start",
            "late-name-keeps-order",
        ],
    )
    async def test_partial_call_lifecycle(
        self,
        fake_agent: FakeACPAgent,
        emit: tuple[Emit, ...],
        updates: list[ToolCallStart | ToolCallProgress],
        expected: list[dict[str, str]],
    ) -> None:
        @fake_agent.on_prompt
        async def turn(agent: FakeACPAgent, sid: str) -> None:
            for update in updates:
                await agent.emit(sid, update)

        recorder = ActivityRecorder()
        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True), emit=emit
        ) as session:
            await session.send("work", tools=observed_tools(recorder))
        assert recorder.events == expected
