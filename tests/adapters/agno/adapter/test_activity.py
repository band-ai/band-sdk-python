from __future__ import annotations

import pytest
from agno.models.message import Message
from agno.run.agent import RunOutput

from band.core.types import Emit
from band.testing.activity import ActivityRecorder
from tests.adapters.agno.helpers import tool_events, tool_execution


class TestAgnoStreamActivity:
    @pytest.mark.parametrize("narrated", [False, True])
    async def test_streamed_tool_activity_preserves_final_response_with_or_without_narration(
        self, make_started_adapter, sample_platform_message, tools, narrated: bool
    ) -> None:
        recorder = ActivityRecorder()
        tools._activity_observer = recorder
        response = RunOutput(
            content="final reply",
            messages=[Message(role="assistant", content="final reply")],
        )
        adapter, agent = await make_started_adapter(
            response,
            emit=(Emit.TOOL_CALLS,) if narrated else (),
            events=tool_events(
                tool_execution(
                    "shell",
                    args={"command": "private-argument"},
                    result="private-result",
                )
            ),
        )
        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        recorder.assert_completed_tool("shell")
        assert not tools.messages_sent
        assert adapter._message_history["room-1"][-1].content == "final reply"
        assert bool(tools.events_sent) is narrated
        assert agent.arun.call_args.kwargs["stream"] is True
