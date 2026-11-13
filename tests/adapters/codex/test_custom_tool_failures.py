"""Codex adapter: a failing custom tool reaches Codex as a failed call, and the turn goes on."""

from __future__ import annotations

import json
from typing import Any

import pytest

from band.core.exceptions import BandError
from band.runtime.tools.schema import tool_execution_failure, tool_result_text
from band.testing import FakeAgentTools, failure_reports
from tests.adapters.codex.kit import ROOM_ID, CodexTurn, run_codex_turn
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)
from tests.hostcontrol import host_control_errors
from tests.runtime.customtools.kit import API_UNAVAILABLE, WeatherInput, failing_tool

NO_FORECAST = "no forecast for that city"
SENT = {"status": "ok"}  # FakeAgentTools' result for any platform tool


class TestCustomToolFailures:
    @staticmethod
    async def run_weather_turn(handler: Any) -> CodexTurn:
        """One turn where Codex calls ``weather`` and then replies."""
        return await run_codex_turn(
            events=[
                tool_call_request(1, "weather", {"city": "Paris"}),
                tool_call_request(
                    2, "band_send_message", {"content": "down", "mentions": []}
                ),
                turn_completed(),
            ],
            additional_tools=[(WeatherInput, handler)],
        )

    @staticmethod
    def answer(text: str, *, success: bool) -> dict[str, Any]:
        return {
            "contentItems": [{"type": "inputText", "text": text}],
            "success": success,
        }

    @pytest.mark.asyncio
    async def test_raised_failure_reaches_codex_and_the_turn_replies(self) -> None:
        turn = await self.run_weather_turn(failing_tool)

        failure = tool_execution_failure("weather", ValueError(API_UNAVAILABLE))
        assert turn.client.responses == [
            (1, self.answer(failure, success=False)),
            (2, self.answer(json.dumps(SENT), success=True)),
        ]
        assert failure_reports(turn.tools) == []
        assert turn.tools.turn.replied

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "value",
        [{"ok": False, "reason": NO_FORECAST}, f"Error: {NO_FORECAST}"],
        ids=["structured", "legacy-string"],
    )
    async def test_returned_failure_value_reaches_codex_as_a_failure(
        self, value: object
    ) -> None:
        async def weather(args: WeatherInput) -> object:
            return value

        turn = await self.run_weather_turn(weather)

        assert turn.client.responses == [
            (1, self.answer(tool_result_text(value), success=False)),
            (2, self.answer(json.dumps(SENT), success=True)),
        ]
        assert failure_reports(turn.tools) == []
        assert turn.tools.turn.replied

    @pytest.mark.parametrize("control", host_control_errors(), ids=type)
    async def test_host_control_is_not_returned_as_a_tool_failure(
        self, control: BandError
    ) -> None:
        async def weather(args: WeatherInput) -> str:
            raise control

        with pytest.raises(type(control)) as caught:
            await self.run_weather_turn(weather)

        assert caught.value is control

    async def test_failed_result_reporting_does_not_answer_the_tool_twice(self) -> None:
        failure = RuntimeError("tool event transport unavailable")
        tools = FakeAgentTools(room_id=ROOM_ID)

        async def weather(args: WeatherInput) -> str:
            tools.send_event_error = failure
            return "sunny"

        client = FakeCodexClient(events=[])
        adapter = make_codex_adapter(client, additional_tools=[(WeatherInput, weather)])

        with pytest.raises(RuntimeError) as caught:
            await adapter._handle_server_request(
                client=client,
                tools=tools,
                audience=[],
                room_id=ROOM_ID,
                event=tool_call_request(1, "weather", {"city": "Paris"}),
            )

        assert caught.value is failure
        assert client.responses == [(1, self.answer("sunny", success=True))]
