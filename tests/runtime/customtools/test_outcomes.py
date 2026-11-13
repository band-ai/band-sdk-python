"""A custom tool's failure becomes a result the model reads; host control propagates."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from band_sdk_core import AgentFailure

from band.core.exceptions import (
    CleanupIncompleteError,
    InputNotAccepted,
    RoomExecutionStoppedError,
    TerminalProviderFailure,
)
from band.runtime.custom_tools import (
    declares_turn_effect,
    run_custom_tool,
    run_validated_custom_tool,
)
from band.runtime.tools.schema import (
    ToolCallOutcome,
    tool_execution_failure,
    tool_result_text,
)
from band.runtime.tools.types import TurnEffect
from band.testing import FakeAgentTools
from tests.runtime.customtools.kit import (
    API_UNAVAILABLE,
    FrameworkSignal,
    WeatherInput,
    failing_tool,
)

CITY = {"city": "NYC"}
UNAVAILABLE = tool_execution_failure("weather", ValueError(API_UNAVAILABLE))


class TestCustomToolOutcomes:
    async def test_a_result_is_returned_and_its_effect_recorded(self) -> None:
        @declares_turn_effect(TurnEffect.ACT)
        async def lookup(args: WeatherInput) -> str:
            return f"Sunny in {args.city}"

        tools = FakeAgentTools()

        outcome = await run_custom_tool((WeatherInput, lookup), CITY, tools=tools)

        assert outcome == ToolCallOutcome(value="Sunny in NYC", ok=True)
        assert tools.turn.complete

    async def test_a_raising_handler_is_a_failed_result_without_its_effect(
        self,
    ) -> None:
        @declares_turn_effect(TurnEffect.ACT)
        async def lookup(args: WeatherInput) -> str:
            raise ValueError(API_UNAVAILABLE)

        tools = FakeAgentTools()

        outcome = await run_custom_tool((WeatherInput, lookup), CITY, tools=tools)

        assert outcome == ToolCallOutcome(
            value=UNAVAILABLE, ok=False, error_message=UNAVAILABLE
        )
        assert not tools.turn.complete

    async def test_invalid_arguments_are_a_failed_result_without_running(
        self,
    ) -> None:
        calls: list[WeatherInput] = []

        async def lookup(args: WeatherInput) -> str:
            calls.append(args)
            return "unreachable"

        outcome = await run_custom_tool((WeatherInput, lookup), {}, tools=None)

        message = "Invalid arguments for weather: city: Field required"
        assert outcome == ToolCallOutcome(
            value=message, ok=False, error_message=message
        )
        assert calls == []

    @pytest.mark.parametrize("value", [{"ok": False, "reason": "busy"}, "Error: busy"])
    async def test_a_failure_value_is_a_failed_result_without_its_effect(
        self, value: Any
    ) -> None:
        @declares_turn_effect(TurnEffect.ACT)
        async def lookup(args: WeatherInput) -> Any:
            return value

        tools = FakeAgentTools()

        outcome = await run_custom_tool((WeatherInput, lookup), CITY, tools=tools)

        assert outcome == ToolCallOutcome(
            value=value, ok=False, error_message=tool_result_text(value)
        )
        assert not tools.turn.complete

    @pytest.mark.parametrize(
        "control",
        [
            InputNotAccepted("not accepted"),
            RoomExecutionStoppedError("room-1"),
            CleanupIncompleteError("cleanup still running"),
            TerminalProviderFailure(AgentFailure("provider", "terminal")),
            FrameworkSignal("retry"),
            asyncio.CancelledError(),
        ],
        ids=type,
    )
    async def test_host_control_and_framework_signals_propagate(
        self, control: BaseException
    ) -> None:
        async def lookup(args: WeatherInput) -> str:
            raise control

        with pytest.raises(type(control)):
            await run_custom_tool(
                (WeatherInput, lookup),
                CITY,
                tools=None,
                native_signals=(FrameworkSignal,),
            )

    async def test_prevalidated_arguments_follow_the_same_failure_rule(
        self,
    ) -> None:
        outcome = await run_validated_custom_tool(
            (WeatherInput, failing_tool), WeatherInput(**CITY), tools=None
        )

        assert outcome == ToolCallOutcome(
            value=UNAVAILABLE, ok=False, error_message=UNAVAILABLE
        )
