"""Failed custom declines remain native errors and allow model recovery."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("strands", reason="strands extra not installed")

from strands.types.tools import ToolResult

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
from tests.adapters.strands.declinepeer import DeclineModel, NativeStatus, results
from tests.adapters.strands.kit import SEND_TURN, _run_message, run_judged_turn

REJECTION_NOTE = "refused"


class TestCustomDeclineFailure:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["portable", "native"])
    @pytest.mark.parametrize(
        "output",
        [
            {"ok": False, "error": REJECTION_NOTE},
            f"Error: {REJECTION_NOTE}",
            f"Error executing decline: {REJECTION_NOTE}",
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
            "status": NativeStatus.ERROR,
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
    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            (
                "portable",
                [
                    {
                        "toolUseId": "call-decline",
                        "status": NativeStatus.ERROR,
                        "content": [
                            {
                                "text": f"Error executing tool 'decline': {REJECTION_NOTE}"
                            }
                        ],
                    }
                ],
            ),
            (
                "native",
                [
                    {
                        "toolUseId": "call-decline",
                        "status": NativeStatus.ERROR,
                        "content": [{"text": f"Error: {REJECTION_NOTE}"}],
                    }
                ],
            ),
        ],
    )
    async def test_handler_exception_is_recoverable_without_decline(
        self,
        kind: str,
        expected: list[ToolResult],
        custom_decline_tools: Callable[..., Any],
    ) -> None:
        async def fail(note: str) -> str:
            raise ValueError(note)

        model = DeclineModel(
            [ToolTurn("decline", {"note": REJECTION_NOTE}), TextTurn("")]
        )
        adapter = StrandsAdapter(
            llm=model, additional_tools=[custom_decline_tools(fail)[kind]]
        )
        await adapter.on_started("Bot", "A bot")
        tools = FakeAgentTools()

        await _run_message(adapter, tools)

        assert len(model.requests) == 2
        assert results(model.requests[1]) == expected
        assert not tools.turn.complete
        assert not tools.turn.replied

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            (
                "portable",
                [
                    {
                        "toolUseId": "call-decline",
                        "status": NativeStatus.ERROR,
                        "content": [
                            {
                                "text": "Error executing tool 'decline': Invalid arguments for decline: note: Field required"
                            }
                        ],
                    }
                ],
            ),
            (
                "native",
                [
                    {
                        "toolUseId": "call-decline",
                        "status": NativeStatus.ERROR,
                        "content": [
                            {
                                "text": (
                                    "Error: Validation failed for input parameters: 1 validation error for NativeTool\n"
                                    "note\n"
                                    "  Field required [type=missing, input_value={}, input_type=dict]\n"
                                    "    For further information visit https://errors.pydantic.dev/2.12/v/missing"
                                )
                            }
                        ],
                    }
                ],
            ),
        ],
    )
    async def test_invalid_arguments_never_invoke_the_decline(
        self,
        kind: str,
        expected: list[ToolResult],
        custom_decline_tools: Callable[..., Any],
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
        assert results(model.requests[1]) == expected
        assert len(model.requests) == 3
        tools.assert_message_sent(content="hi", count=1)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("output", "content"),
        [
            (
                {"status": NativeStatus.ERROR, "content": [{"text": REJECTION_NOTE}]},
                [{"text": REJECTION_NOTE}],
            ),
            (
                {"status": NativeStatus.SUCCESS, "content": [{"json": {"ok": False}}]},
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

        model = DeclineModel(
            [ToolTurn("decline", {"note": REJECTION_NOTE}), TextTurn("")]
        )
        adapter = StrandsAdapter(
            llm=model, additional_tools=[custom_decline_tools(fail)["native"]]
        )
        await adapter.on_started("Bot", "A bot")
        tools = FakeAgentTools()

        await _run_message(adapter, tools)

        assert results(model.requests[1]) == [
            {
                "toolUseId": "call-decline",
                "status": NativeStatus.ERROR,
                "content": content,
            }
        ]
        assert len(model.requests) == 2
        assert not tools.turn.complete
