"""Successful custom declines stop native and ReAct CrewAI execution."""

from __future__ import annotations

from typing import Any

import pytest

from band.runtime.custom_tools import declares_turn_effect, get_custom_tool_name
from band.runtime.tools import TurnEffect
from band.testing import failure_reports
from tests.adapters.crewaiturnend.kit import (
    DECLINED_RESULT,
    WORKED_RESULT,
    DeclineInput,
    WorkInput,
    legacy_terminal,
    run_crewai_turn,
    run_flow_turn,
    scripted_model,
)
from tests.framework_conformance.turnprobes import ToolCall, turn_tools, undeclared


class TestCustomDecline:
    @pytest.mark.parametrize("run_turn", [run_crewai_turn, run_flow_turn])
    @pytest.mark.parametrize("native", [True, False], ids=["native", "react"])
    async def test_success_ends_after_one_model_request(
        self, run_turn: Any, native: bool
    ) -> None:
        calls = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(args: DeclineInput) -> str:
            calls.append(args.reason)
            return DECLINED_RESULT

        model = scripted_model(
            native, ToolCall(get_custom_tool_name(DeclineInput), {"reason": "done"})
        )
        tools = turn_tools()

        await run_turn(model, tools, custom_tools=[(DeclineInput, decline)])

        assert model.asked == 1
        assert calls == ["done"]
        assert tools.turn.replied
        assert failure_reports(tools) == []

    @pytest.mark.parametrize("run_turn", [run_crewai_turn, run_flow_turn])
    @pytest.mark.parametrize("native", [True, False], ids=["native", "react"])
    @pytest.mark.parametrize(
        "failure",
        [
            {"ok": False, "reason": "unavailable"},
            "Error: unavailable",
        ],
        ids=["mapping", "legacy"],
    )
    async def test_failed_decline_allows_the_next_step(
        self, run_turn: Any, native: bool, failure: Any
    ) -> None:
        calls = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(args: DeclineInput) -> Any:
            calls.append(args.reason)
            return failure

        @declares_turn_effect(TurnEffect.ACT)
        async def work(args: WorkInput) -> str:
            calls.append(args.value)
            return WORKED_RESULT

        model = scripted_model(
            native,
            ToolCall(get_custom_tool_name(DeclineInput), {"reason": "try"}),
            ToolCall(get_custom_tool_name(WorkInput), {"value": "next"}),
        )
        tools = turn_tools()

        await run_turn(
            model, tools, custom_tools=[(DeclineInput, decline), (WorkInput, work)]
        )

        assert model.asked == 3
        assert calls == ["try", "next"]

    @pytest.mark.parametrize("run_turn", [run_crewai_turn, run_flow_turn])
    @pytest.mark.parametrize("native", [True, False], ids=["native", "react"])
    @pytest.mark.parametrize(
        "declare",
        [
            undeclared,
            declares_turn_effect(TurnEffect.OBSERVE),
            declares_turn_effect(TurnEffect.ACT),
            declares_turn_effect(TurnEffect.REPLY),
            legacy_terminal,
        ],
    )
    async def test_other_effects_allow_the_next_step(
        self, run_turn: Any, native: bool, declare: Any
    ) -> None:
        calls = []

        async def handler(args: DeclineInput) -> str:
            calls.append(args.reason)
            return "done"

        handler = declare(handler)

        @declares_turn_effect(TurnEffect.ACT)
        async def work(args: WorkInput) -> str:
            calls.append(args.value)
            return WORKED_RESULT

        model = scripted_model(
            native,
            ToolCall(get_custom_tool_name(DeclineInput), {"reason": "work"}),
            ToolCall(get_custom_tool_name(WorkInput), {"value": "next"}),
        )
        tools = turn_tools()

        await run_turn(
            model, tools, custom_tools=[(DeclineInput, handler), (WorkInput, work)]
        )

        assert model.asked == 3
        assert calls == ["work", "next"]

    @pytest.mark.parametrize("run_turn", [run_crewai_turn, run_flow_turn])
    @pytest.mark.parametrize("native", [True, False], ids=["native", "react"])
    async def test_raised_decline_allows_the_next_step(
        self, run_turn: Any, native: bool
    ) -> None:
        calls = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(args: DeclineInput) -> Any:
            calls.append(args.reason)
            raise ValueError("unavailable")

        @declares_turn_effect(TurnEffect.ACT)
        async def work(args: WorkInput) -> str:
            calls.append(args.value)
            return WORKED_RESULT

        model = scripted_model(
            native,
            ToolCall(get_custom_tool_name(DeclineInput), {"reason": "try"}),
            ToolCall(get_custom_tool_name(WorkInput), {"value": "next"}),
        )
        tools = turn_tools()

        await run_turn(
            model, tools, custom_tools=[(DeclineInput, decline), (WorkInput, work)]
        )

        assert model.asked == 3
        assert calls == ["try", "next"]
