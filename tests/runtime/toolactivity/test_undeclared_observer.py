from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from band.integrations.claude_sdk.dedup_tools import DedupingAgentTools
from band.integrations.mcp.engine import dispatch_tool
from band.runtime.tools import AgentTools
from band.runtime.tools.registry import TOOL_DEFINITIONS
from band.runtime.tools.types import BandTool
from band.testing.activity import ActivityRecorder

SEND_EVENT = TOOL_DEFINITIONS[BandTool.SEND_EVENT]
ARGUMENTS = {"content": "model", "message_type": "thought"}


class TestUndeclaredObserver:
    async def test_a_proxy_reports_through_the_tools_it_wraps(
        self, tools: AgentTools, recorder: ActivityRecorder
    ) -> None:
        await dispatch_tool(DedupingAgentTools(tools), SEND_EVENT, ARGUMENTS)
        recorder.assert_completed_tool(BandTool.SEND_EVENT)

    async def test_tools_without_a_declared_observer_run_unreported(self) -> None:
        tools = MagicMock(send_event=AsyncMock(return_value={"id": "evt-1"}))
        await dispatch_tool(tools, SEND_EVENT, ARGUMENTS)
        tools.send_event.assert_awaited_once()
