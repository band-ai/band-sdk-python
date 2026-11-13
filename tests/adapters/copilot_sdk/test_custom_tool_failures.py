"""A failing custom tool reaches Copilot as a failed tool result, and the turn goes on."""

from __future__ import annotations

from typing import Any

import pytest

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE
from band.runtime.tools.schema import tool_execution_failure, tool_result_text
from band.testing import failure_reports
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    FakeCopilotSession,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_event,
)
from tests.runtime.customtools.kit import API_UNAVAILABLE, WeatherInput, failing_tool

pytestmark = requires_copilot_sdk

if _COPILOT_SDK_AVAILABLE:
    from copilot import ToolInvocation, ToolResult

REPLY = "The weather service is down."
NO_FORECAST = "no forecast for that city"


class TestCustomToolFailures:
    @staticmethod
    async def run_weather_turn(handler: Any) -> tuple[list[ToolResult], Any]:
        """One judged turn where the model calls ``weather`` and then replies."""
        results: list[ToolResult] = []

        async def model_calls_weather(session: FakeCopilotSession) -> None:
            results.append(
                await session.find_tool("weather").handler(
                    ToolInvocation(
                        tool_call_id="c1",
                        tool_name="weather",
                        arguments={"city": "Paris"},
                    )
                )
            )

        client = FakeCopilotClient(
            reply_content=REPLY, turn_events=[model_calls_weather]
        )
        adapter = await make_started_adapter(
            client, additional_tools=[(WeatherInput, handler)]
        )
        tools = ToolSchemaFakeTools()
        await run_event(adapter, tools)
        return results, tools

    @pytest.mark.asyncio
    async def test_raised_failure_reaches_the_model_and_the_turn_replies(self):
        results, tools = await self.run_weather_turn(failing_tool)

        failure = tool_execution_failure("weather", ValueError(API_UNAVAILABLE))
        assert results == [
            ToolResult(
                text_result_for_llm=failure, result_type="failure", error=failure
            )
        ]
        assert failure_reports(tools) == []
        assert tools.chat == [REPLY]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "value",
        [{"ok": False, "reason": NO_FORECAST}, f"Error: {NO_FORECAST}"],
        ids=["structured", "legacy-string"],
    )
    async def test_returned_failure_value_reaches_the_model_as_a_failure(
        self, value: object
    ):
        async def weather(args: WeatherInput) -> object:
            return value

        results, tools = await self.run_weather_turn(weather)

        failure = tool_result_text(value)
        assert results == [
            ToolResult(
                text_result_for_llm=failure, result_type="failure", error=failure
            )
        ]
        assert failure_reports(tools) == []
        assert tools.chat == [REPLY]
