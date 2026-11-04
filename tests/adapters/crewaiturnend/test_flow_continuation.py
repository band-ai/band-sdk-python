"""An inner CrewAI decline leaves the outer Flow in control."""

from __future__ import annotations

from typing import Any

import pytest

from band.adapters.crewai_flow import DirectResponseDecision
from band.runtime.custom_tools import declares_turn_effect, get_custom_tool_name
from band.runtime.tools import TurnEffect
from band.testing import failure_reports
from tests.adapters.crewaiturnend.kit import (
    DECLINED_RESULT,
    DeclineInput,
    run_flow_turn,
    scripted_model,
)
from tests.framework_conformance.turnprobes import ToolCall, turn_tools

OUTER_RESPONSE = "Outer response"


class TestFlowContinuation:
    @pytest.mark.parametrize("native", [True, False], ids=["native", "react"])
    async def test_subcrew_decline_allows_an_explicit_outer_response(
        self, native: bool
    ) -> None:
        actions = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(args: DeclineInput) -> Any:
            actions.append(args.reason)
            return DECLINED_RESULT

        async def continue_flow(runtime: Any) -> Any:
            actions.append("outer action")
            # Direct runtime custom calls also return normally after a decline.
            result = await runtime.call_tool(
                get_custom_tool_name(DeclineInput), {"reason": "direct"}
            )
            assert result == DECLINED_RESULT
            return DirectResponseDecision(
                decision="direct_response", content=OUTER_RESPONSE
            ).model_dump()

        model = scripted_model(
            native, ToolCall(get_custom_tool_name(DeclineInput), {"reason": "inner"})
        )
        tools = turn_tools()

        await run_flow_turn(
            model, tools, custom_tools=[(DeclineInput, decline)], after=continue_flow
        )

        assert model.asked == 1
        assert actions == ["inner", "outer action", "direct"]
        assert tools.chat == [OUTER_RESPONSE]
        assert failure_reports(tools) == []
