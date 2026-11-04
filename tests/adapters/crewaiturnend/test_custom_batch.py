"""CrewAI executes native calls in order until a successful custom decline."""

from __future__ import annotations

from typing import Any

import pytest

from band.runtime.custom_tools import declares_turn_effect, get_custom_tool_name
from band.runtime.tools import TurnEffect
from tests.adapters.crewaiturnend.kit import (
    DECLINED_RESULT,
    WORKED_RESULT,
    DeclineInput,
    WorkInput,
    run_crewai_turn,
    run_flow_turn,
)
from tests.crewaiscript import ScriptedLLM, step
from tests.framework_conformance.turnprobes import ToolCall, turn_tools


class TestCustomBatch:
    @pytest.mark.parametrize("run_turn", [run_crewai_turn, run_flow_turn])
    @pytest.mark.parametrize(
        "order,expected",
        [("work-first", ["work", "decline"]), ("decline-first", ["decline"])],
    )
    async def test_successful_decline_skips_only_later_native_calls(
        self, run_turn: Any, order: str, expected: Any
    ) -> None:
        calls = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(args: DeclineInput) -> Any:
            calls.append("decline")
            return DECLINED_RESULT

        @declares_turn_effect(TurnEffect.ACT)
        async def work(args: WorkInput) -> Any:
            calls.append(args.value)
            return WORKED_RESULT

        ordered = {
            "work-first": [
                ToolCall(get_custom_tool_name(WorkInput), {"value": "work"}),
                ToolCall(get_custom_tool_name(DeclineInput), {"reason": "done"}),
            ],
            "decline-first": [
                ToolCall(get_custom_tool_name(DeclineInput), {"reason": "done"}),
                ToolCall(get_custom_tool_name(WorkInput), {"value": "work"}),
            ],
        }
        model = ScriptedLLM(step(*ordered[order]))
        tools = turn_tools()

        await run_turn(
            model, tools, custom_tools=[(DeclineInput, decline), (WorkInput, work)]
        )

        assert model.asked == 1
        assert calls == expected

    @pytest.mark.parametrize("run_turn", [run_crewai_turn, run_flow_turn])
    async def test_failed_decline_keeps_later_native_calls_executable(
        self, run_turn: Any
    ) -> None:
        calls = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(args: DeclineInput) -> Any:
            calls.append("decline")
            return {"ok": False}

        @declares_turn_effect(TurnEffect.ACT)
        async def work(args: WorkInput) -> Any:
            calls.append(args.value)
            return WORKED_RESULT

        model = ScriptedLLM(
            step(
                ToolCall(get_custom_tool_name(DeclineInput), {"reason": "try"}),
                ToolCall(get_custom_tool_name(WorkInput), {"value": "work"}),
            ),
            "Done.",
        )
        tools = turn_tools()

        await run_turn(
            model, tools, custom_tools=[(DeclineInput, decline), (WorkInput, work)]
        )

        assert model.asked == 2
        assert calls == ["decline", "work"]
