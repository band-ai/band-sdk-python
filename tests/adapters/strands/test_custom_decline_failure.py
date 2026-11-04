"""Failed custom declines remain native errors and allow model recovery."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("strands", reason="strands extra not installed")

from band.adapters.strands import StrandsAdapter
from band.converters.strands import StrandsHistoryConverter
from band.core.types import MessageType
from band.testing import (
    FakeAgentTools,
    TextTurn,
    ToolTurn,
    events_of_type,
    failure_reports,
)
from tests.adapters.strands.declinepeer import DeclineModel, results
from tests.adapters.strands.kit import SEND_TURN, _run_message, run_judged_turn


class TestCustomDeclineFailure:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["portable", "native"])
    @pytest.mark.parametrize(
        "output",
        [
            {"ok": False, "error": "refused"},
            "Error: refused",
            "Error executing decline: refused",
        ],
    )
    async def test_failed_return_recovers_with_truthful_history_and_events(
        self, kind: str, output: Any, custom_decline_tools: Callable[..., Any]
    ) -> None:
        calls: list[str] = []

        async def fail(note: str) -> Any:
            calls.append(note)
            return output

        model = DeclineModel(
            [ToolTurn("decline", {"note": "go"}), SEND_TURN, TextTurn("")]
        )
        adapter = StrandsAdapter(
            llm=model, additional_tools=[custom_decline_tools(fail)[kind]]
        )
        await adapter.on_started("Bot", "A bot")
        tools = FakeAgentTools()

        await run_judged_turn(adapter, tools)

        expected = {
            "toolUseId": "call-decline",
            "status": "error",
            "content": [
                {"text": output if isinstance(output, str) else json.dumps(output)}
            ],
        }
        assert results(model.requests[1]) == [expected]
        assert calls == ["go"]
        assert len(model.requests) == 3
        tools.assert_message_sent(content="hi", count=1)
        result_events = events_of_type(tools, MessageType.TOOL_RESULT)
        assert [
            json.loads(event["content"])["is_error"] for event in result_events
        ] == [True, False]
        rehydrated = StrandsHistoryConverter(agent_name="Bot").convert(
            [
                {"role": "assistant", "content": event["content"], **event}
                for event in tools.events_sent
                if event["message_type"]
                in (MessageType.TOOL_CALL, MessageType.TOOL_RESULT)
            ]
        )
        assert results(rehydrated)[0] == expected
        assert failure_reports(tools) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["portable", "native"])
    async def test_handler_exception_is_recoverable_without_decline(
        self, kind: str, custom_decline_tools: Callable[..., Any]
    ) -> None:
        async def fail(note: str) -> str:
            raise ValueError(note)

        model = DeclineModel([ToolTurn("decline", {"note": "refused"}), TextTurn("")])
        adapter = StrandsAdapter(
            llm=model, additional_tools=[custom_decline_tools(fail)[kind]]
        )
        await adapter.on_started("Bot", "A bot")
        tools = FakeAgentTools()

        await _run_message(adapter, tools)

        assert len(model.requests) == 2
        assert results(model.requests[1])[0]["status"] == "error"
        assert "refused" in results(model.requests[1])[0]["content"][0]["text"]
        assert not tools.turn.complete
        assert not tools.turn.replied

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["portable", "native"])
    async def test_invalid_arguments_never_invoke_the_decline(
        self, kind: str, custom_decline_tools: Callable[..., Any]
    ) -> None:
        calls: list[str] = []

        async def decline(note: str) -> str:
            calls.append(note)
            return note

        model = DeclineModel([ToolTurn("decline", {}), SEND_TURN, TextTurn("")])
        adapter = StrandsAdapter(
            llm=model, additional_tools=[custom_decline_tools(decline)[kind]]
        )
        await adapter.on_started("Bot", "A bot")
        tools = FakeAgentTools()

        await run_judged_turn(adapter, tools)

        assert calls == []
        assert results(model.requests[1])[0]["status"] == "error"
        assert len(model.requests) == 3
        tools.assert_message_sent(content="hi", count=1)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("output", "content"),
        [
            (
                {"status": "error", "content": [{"text": "refused"}]},
                [{"text": "refused"}],
            ),
            (
                {"status": "success", "content": [{"json": {"ok": False}}]},
                [{"json": {"ok": False}}],
            ),
        ],
    )
    async def test_native_failure_status_and_json_blocks_never_decline(
        self,
        output: dict[str, Any],
        content: list[dict[str, Any]],
        custom_decline_tools: Callable[..., Any],
    ) -> None:
        async def fail(note: str) -> dict[str, Any]:
            return output

        model = DeclineModel([ToolTurn("decline", {"note": "refused"}), TextTurn("")])
        adapter = StrandsAdapter(
            llm=model, additional_tools=[custom_decline_tools(fail)["native"]]
        )
        await adapter.on_started("Bot", "A bot")
        tools = FakeAgentTools()

        await _run_message(adapter, tools)

        assert results(model.requests[1]) == [
            {
                "toolUseId": "call-decline",
                "status": "error",
                "content": content,
            }
        ]
        assert len(model.requests) == 2
        assert not tools.turn.complete
