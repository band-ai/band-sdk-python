from __future__ import annotations

from acp.schema import ToolCallProgress, ToolCallStart

from band.integrations.acp.collecting import ACPCollectingClient
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder


class TestACPActivity:
    async def test_activity_precedes_buffered_narration_and_ignores_private_titles(
        self,
    ) -> None:
        client = ACPCollectingClient()
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        tools._activity_observer = recorder
        client.set_activity_tools("session", tools)
        await client.session_update(
            "session",
            ToolCallStart(
                sessionUpdate="tool_call",
                toolCallId="call",
                title="private command and arguments",
                status="pending",
            ),
        )
        assert recorder.events == [{"kind": "tool_call", "name": "unknown"}]
        await client.session_update(
            "session",
            ToolCallProgress(
                sessionUpdate="tool_call_update",
                toolCallId="call",
                title="private-result",
                status="in_progress",
                rawOutput={"text": "private-result"},
            ),
        )
        assert recorder.events == [{"kind": "tool_call", "name": "unknown"}]
        await client.session_update(
            "session",
            ToolCallProgress(
                sessionUpdate="tool_call_update", toolCallId="call", status="completed"
            ),
        )
        recorder.assert_completed_tool("unknown")
        client.set_activity_tools("session", None)
        await client.session_update(
            "session",
            ToolCallStart(
                sessionUpdate="tool_call", toolCallId="late", title="private-late"
            ),
        )
        assert recorder.events[-1] == {"kind": "reasoning"}
