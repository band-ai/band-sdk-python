"""CrewAI failure policy and validation remain authoritative for terminal tools."""

from __future__ import annotations

from typing import Any

import pytest
from crewai.tools.tool_failure import ToolExecutionFailedError

from band.runtime.custom_tools import declares_turn_effect, get_custom_tool_name
from band.runtime.tools import TurnEffect
from band.testing import failure_reports
from tests.adapters.crewaiturnend.kit import (
    DECLINED_RESULT,
    WORKED_RESULT,
    DeclineInput,
    UnavailableDeclineTools,
    WorkInput,
    run_crewai_turn,
    run_flow_turn,
    scripted_model,
)
from tests.framework_conformance.turnprobes import ToolCall, turn_tools


class TestNativeFailurePolicy:
    @pytest.mark.parametrize("native", [True, False], ids=["native", "react"])
    async def test_explicit_raise_policy_aborts_instead_of_finalizing(
        self, native: bool
    ) -> None:
        calls = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(args: DeclineInput) -> Any:
            calls.append(args.reason)
            raise ValueError("unavailable")

        model = scripted_model(
            native, ToolCall(get_custom_tool_name(DeclineInput), {"reason": "try"})
        )
        tools = turn_tools()

        with pytest.raises(ToolExecutionFailedError):
            await run_crewai_turn(
                model,
                tools,
                custom_tools=[(DeclineInput, decline)],
                failure_policy="raise",
            )

        assert model.asked == 1
        assert calls == ["try"]
        assert not tools.turn.replied

    @pytest.mark.parametrize("run_turn", [run_crewai_turn, run_flow_turn])
    @pytest.mark.parametrize("native", [True, False], ids=["native", "react"])
    async def test_invalid_decline_arguments_allow_a_corrected_call(
        self, run_turn: Any, native: bool
    ) -> None:
        calls = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(args: DeclineInput) -> Any:
            calls.append(args.reason)
            return DECLINED_RESULT

        model = scripted_model(
            native,
            ToolCall(get_custom_tool_name(DeclineInput), {"reason": ["invalid"]}),
            ToolCall(get_custom_tool_name(DeclineInput), {"reason": "valid"}),
        )
        tools = turn_tools()

        await run_turn(model, tools, custom_tools=[(DeclineInput, decline)])

        assert model.asked == 2
        assert calls == ["valid"]
        assert tools.turn.replied

    @pytest.mark.parametrize("native", [True, False], ids=["native", "react"])
    async def test_flow_reports_native_raise_policy_without_continuing(
        self, native: bool
    ) -> None:
        calls = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(args: DeclineInput) -> Any:
            calls.append(args.reason)
            raise ValueError("unavailable")

        model = scripted_model(
            native, ToolCall(get_custom_tool_name(DeclineInput), {"reason": "try"})
        )
        tools = turn_tools()
        await run_flow_turn(
            model, tools, custom_tools=[(DeclineInput, decline)], failure_policy="raise"
        )

        assert model.asked == 1
        assert calls == ["try"]
        assert not tools.turn.replied
        assert len(failure_reports(tools)) == 1
        assert failure_reports(tools)[0][0] == "crewai_flow"

    @pytest.mark.parametrize("run_turn", [run_crewai_turn, run_flow_turn])
    @pytest.mark.parametrize("native", [True, False], ids=["native", "react"])
    async def test_failed_platform_decline_allows_model_recovery(
        self, run_turn: Any, native: bool
    ) -> None:
        calls = []

        @declares_turn_effect(TurnEffect.ACT)
        async def work(args: WorkInput) -> Any:
            calls.append(args.value)
            return WORKED_RESULT

        model = scripted_model(
            native,
            ToolCall("band_no_reply", {"reason": "try"}),
            ToolCall(get_custom_tool_name(WorkInput), {"value": "recovered"}),
        )
        tools = UnavailableDeclineTools()

        await run_turn(model, tools, custom_tools=[(WorkInput, work)])

        assert model.asked == 3
        assert calls == ["recovered"]
        assert not tools.turn.replied
        assert failure_reports(tools) == []
