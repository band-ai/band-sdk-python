from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

pytest.importorskip("strands", reason="strands extra not installed")

from band.adapters.strands import StrandsAdapter
from band.runtime.tools import BandTool
from band.testing import FakeAgentTools, TextTurn, ToolTurn
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool
from tests.adapters.strands.kit import run_judged_turn


class TestStrandsToolActivity:
    async def test_model_tool_calls_show_in_order_while_adapter_telemetry_stays_quiet(
        self,
        scripted: Callable[..., Awaitable[StrandsAdapter]],
        tools: FakeAgentTools,
    ) -> None:
        recorder = ActivityRecorder()
        recorder.observing(tools)
        adapter = await scripted(
            ToolTurn(
                BandTool.SEND_EVENT, {"content": "model", "message_type": "thought"}
            ),
            ToolTurn(
                BandTool.SEND_MESSAGE, {"content": "done", "mentions": ["@tester"]}
            ),
            TextTurn("finished"),
        )

        await run_judged_turn(adapter, tools)

        assert recorder.events == [
            shown_tool(BandTool.SEND_EVENT),
            SHOWN_REASONING,
            shown_tool(BandTool.SEND_MESSAGE),
            SHOWN_REASONING,
        ]
        assert "model" in [event["content"] for event in tools.events_sent]
