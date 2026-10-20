from __future__ import annotations

import pytest

pytest.importorskip("strands")

from strands.hooks.events import AfterToolCallEvent, BeforeToolCallEvent

from band.adapters.strands import BandTurnHooks
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder


class TestStrandsActivity:
    @pytest.mark.parametrize("status", ["success", "error"])
    async def test_native_hook_reports_without_narration(self, status: str) -> None:
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        tools._activity_observer = recorder
        hooks = BandTurnHooks(tools, emit_execution=False, custom_effects={})
        call = {
            "name": "shell",
            "input": {"command": "private-argument"},
            "toolUseId": "call",
        }
        await hooks._on_before_tool(
            BeforeToolCallEvent(
                agent=None, invocation_state={}, tool_use=call, selected_tool=None
            )
        )
        assert recorder.events == [{"kind": "tool_call", "name": "shell"}]
        await hooks._on_after_tool(
            AfterToolCallEvent(
                agent=None,
                selected_tool=None,
                invocation_state={},
                tool_use=call,
                result={
                    "toolUseId": "call",
                    "status": status,
                    "content": [{"text": "private-result"}],
                },
            )
        )
        recorder.assert_completed_tool("shell")
        assert not tools.events_sent
