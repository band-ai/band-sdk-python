"""An inner CrewAI decline leaves the outer Flow in control."""

from __future__ import annotations

from typing import Any

import pytest

from band.adapters.crewai_flow import DirectResponseDecision
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect
from band.testing import failure_reports
from tests.adapters.crewaiturnend.kit import DeclineInput, run_flow_turn, scripted_model
from tests.framework_conformance.turnprobes import ToolCall, turn_tools


class TestFlowContinuation:
    @pytest.mark.parametrize("native", [True, False], ids=["native", "react"])
    async def test_subcrew_decline_allows_an_explicit_outer_response(
        self, native: bool
    ) -> None:
        actions = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(args: DeclineInput) -> Any:
            actions.append(args.reason)
            return "declined"

        async def continue_flow(runtime: Any) -> Any:
            actions.append("outer action")
            # Direct runtime custom calls also return normally after a decline.
            result = await runtime.call_tool("decline", {"reason": "direct"})
            assert result == "declined"
            return DirectResponseDecision(
                decision="direct_response", content="Outer response"
            ).model_dump()

        model = scripted_model(native, ToolCall("decline", {"reason": "inner"}))
        tools = turn_tools()

        await run_flow_turn(
            model, tools, custom_tools=[(DeclineInput, decline)], after=continue_flow
        )

        assert model.asked == 1
        assert actions == ["inner", "outer action", "direct"]
        assert tools.chat == ["Outer response"]
        assert failure_reports(tools) == []
