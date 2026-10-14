"""``tool_call``/``tool_result`` events for the model's ``self.room`` calls."""

from __future__ import annotations

from band.core.types import Emit
from band.runtime.tools import BandTool
from band.testing import FakeAgentTools
from band.testing.nooa import room_call, scripted_llm
from tests.adapters.nooa.kit import run_turn, started_adapter, tool_events

REPLY = {"content": "Hi Alice", "mentions": ["Alice"]}


class TestToolEvents:
    async def test_tool_calls_are_reported_as_events_when_enabled(
        self, tools: FakeAgentTools
    ) -> None:
        adapter = await started_adapter(
            scripted_llm(room_call(BandTool.SEND_MESSAGE, REPLY)),
            emit={Emit.TOOL_CALLS},
        )

        await run_turn(adapter, tools)

        assert tool_events(tools) == [
            "tool_call band_send_message",
            "tool_result band_send_message",
        ]

    async def test_tool_calls_are_not_reported_when_disabled(
        self, tools: FakeAgentTools
    ) -> None:
        adapter = await started_adapter(
            scripted_llm(room_call(BandTool.SEND_MESSAGE, REPLY)), emit=set()
        )

        await run_turn(adapter, tools)

        assert tool_events(tools) == []
