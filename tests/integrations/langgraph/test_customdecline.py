"""Portable decline effects stop the native loop without hiding tool failures."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool, ToolException

from band.adapters.langgraph import LangGraphAdapter
from band.core.exceptions import InputNotAccepted, RoomExecutionStoppedError
from band.core.types import Emit
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import BandTool, TurnEffect
from band.testing import FakeAgentTools, reported_failures
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input
from tests.integrations.langgraph.declinekit import (
    CUSTOM,
    FAILURE,
    SUCCESS,
    CustomDeclineInput,
    ScriptedModel,
    tool_response,
)


class TestPortableDecline:
    @pytest.mark.parametrize("output", [SUCCESS, {"ok": False}, "Error: declined"])
    async def test_outcome_controls_native_stop_and_error_history(
        self, output: Any
    ) -> None:
        calls = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> Any:
            calls.append(inp.reason)
            return output

        model = ScriptedModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[tool_response()]),
                    AIMessage(
                        content="", tool_calls=[tool_response(BandTool.NO_REPLY)]
                    ),
                ]
            ),
            disable_streaming=True,
        )
        adapter = LangGraphAdapter(
            llm=model, additional_tools=[(CustomDeclineInput, decline)]
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        assert model.requests == (1 if output == SUCCESS else 2)
        assert calls == ["FYI"]
        state = await adapter._simple_checkpointer.aget(
            {"configurable": {"thread_id": ROOM_ID}}
        )
        results = [
            message
            for message in state["channel_values"]["messages"]
            if isinstance(message, ToolMessage)
        ]
        assert results[0].status == ("success" if output == SUCCESS else "error")
        assert reported_failures(tools) == []

    @pytest.mark.parametrize("error", [ValueError(FAILURE), ToolException(FAILURE)])
    async def test_recoverable_error_keeps_safe_text_and_recovery(
        self, error: Exception
    ) -> None:
        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            raise error

        model = ScriptedModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[tool_response()]),
                    AIMessage(
                        content="", tool_calls=[tool_response(BandTool.NO_REPLY)]
                    ),
                ]
            ),
            disable_streaming=True,
        )
        adapter = LangGraphAdapter(
            llm=model, additional_tools=[(CustomDeclineInput, decline)]
        )
        await adapter.on_started("Bot", "Bot")
        await adapter.on_event(turn_input(FakeAgentTools(room_id=ROOM_ID)))
        state = await adapter._simple_checkpointer.aget(
            {"configurable": {"thread_id": ROOM_ID}}
        )
        results = [
            message
            for message in state["channel_values"]["messages"]
            if isinstance(message, ToolMessage)
        ]
        assert (results[0].status, results[0].content) == ("error", FAILURE)
        assert model.requests == 2

    @pytest.mark.parametrize("shadowed", [BandTool.NO_REPLY, CUSTOM])
    async def test_native_winner_masks_portable_and_platform_effects(
        self, shadowed: str
    ) -> None:
        executed = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            raise AssertionError("shadowed portable handler executed")

        @declares_turn_effect(TurnEffect.DECLINE)
        async def native(reason: str) -> str:
            executed.append(reason)
            return SUCCESS

        winner = StructuredTool.from_function(
            coroutine=native, name=shadowed, description="Native tool"
        )
        model = ScriptedModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[tool_response(shadowed)]),
                    AIMessage(
                        content="",
                        tool_calls=[
                            tool_response(
                                BandTool.SEND_MESSAGE,
                                arguments={
                                    "content": "continued",
                                    "mentions": ["alice"],
                                },
                            )
                        ],
                    ),
                    AIMessage(content="done"),
                ]
            ),
            disable_streaming=True,
        )
        adapter = LangGraphAdapter(
            llm=model, additional_tools=[(CustomDeclineInput, decline), winner]
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        assert model.requests == 3
        assert executed == ["FYI"]
        assert tools.tool_calls == [
            {
                "tool_name": BandTool.SEND_MESSAGE,
                "arguments": {"content": "continued", "mentions": ["alice"]},
            }
        ]
        assert reported_failures(tools) == []

    @pytest.mark.parametrize(
        "effect", [TurnEffect.OBSERVE, TurnEffect.ACT, TurnEffect.REPLY]
    )
    async def test_other_effects_leave_the_next_step_executable(
        self, effect: TurnEffect
    ) -> None:
        @declares_turn_effect(effect)
        async def decline(inp: CustomDeclineInput) -> str:
            return SUCCESS

        model = ScriptedModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[tool_response()]),
                    AIMessage(
                        content="", tool_calls=[tool_response(BandTool.NO_REPLY)]
                    ),
                ]
            ),
            disable_streaming=True,
        )
        adapter = LangGraphAdapter(
            llm=model, additional_tools=[(CustomDeclineInput, decline)]
        )
        await adapter.on_started("Bot", "Bot")
        await adapter.on_event(turn_input(FakeAgentTools(room_id=ROOM_ID)))
        assert model.requests == 2

    @pytest.mark.parametrize("sibling_output", [SUCCESS, {"ok": False}])
    async def test_batch_retains_siblings_before_success_or_recovery(
        self, sibling_output: Any
    ) -> None:
        completed = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            completed.append(CUSTOM)
            return SUCCESS

        async def work() -> Any:
            completed.append("work")
            return sibling_output

        sibling = StructuredTool.from_function(
            coroutine=work, name="work", description="Work"
        )
        model = ScriptedModel(
            messages=iter(
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            tool_response(),
                            tool_response("work", arguments={}, call_id="work"),
                        ],
                    ),
                    AIMessage(
                        content="", tool_calls=[tool_response(BandTool.NO_REPLY)]
                    ),
                ]
            ),
            disable_streaming=True,
        )
        adapter = LangGraphAdapter(
            llm=model, additional_tools=[(CustomDeclineInput, decline), sibling]
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        state = await adapter._simple_checkpointer.aget(
            {"configurable": {"thread_id": ROOM_ID}}
        )
        results = [
            message
            for message in state["channel_values"]["messages"]
            if isinstance(message, ToolMessage)
        ]
        assert [(result.name, result.content) for result in results][:2] == [
            (CUSTOM, SUCCESS),
            ("work", SUCCESS if sibling_output == SUCCESS else '{"ok": false}'),
        ]
        assert sorted(completed) == sorted([CUSTOM, "work"])
        assert model.requests == (1 if sibling_output == SUCCESS else 2)

    @pytest.mark.parametrize(
        "error", [InputNotAccepted("not accepted"), RoomExecutionStoppedError(ROOM_ID)]
    )
    async def test_lifecycle_errors_keep_their_identity(self, error: Exception) -> None:
        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            raise error

        model = ScriptedModel(
            messages=iter([AIMessage(content="", tool_calls=[tool_response()])]),
            disable_streaming=True,
        )
        adapter = LangGraphAdapter(
            llm=model, additional_tools=[(CustomDeclineInput, decline)]
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        with pytest.raises(type(error)) as raised:
            await adapter.on_event(turn_input(tools))
        assert raised.value is error
        assert model.requests == 1
        assert not tools.turn.replied

    async def test_invalid_portable_arguments_remain_native_errors(self) -> None:
        executed = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            executed.append(inp)
            return SUCCESS

        model = ScriptedModel(
            messages=iter(
                [
                    AIMessage(
                        content="", tool_calls=[tool_response(arguments={"reason": 42})]
                    ),
                    AIMessage(
                        content="", tool_calls=[tool_response(BandTool.NO_REPLY)]
                    ),
                ]
            ),
            disable_streaming=True,
        )
        adapter = LangGraphAdapter(
            llm=model, additional_tools=[(CustomDeclineInput, decline)]
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        state = await adapter._simple_checkpointer.aget(
            {"configurable": {"thread_id": ROOM_ID}}
        )
        results = [
            message
            for message in state["channel_values"]["messages"]
            if isinstance(message, ToolMessage)
        ]
        assert results[0].status == "error"
        assert "Invalid arguments for customdecline" in results[0].content
        assert executed == []
        assert model.requests == 2

    @pytest.mark.parametrize("emit", [(), Emit.TOOL_CALLS])
    @pytest.mark.parametrize("report_error", [None, RuntimeError("report unavailable")])
    async def test_reporting_cannot_change_successful_decline(
        self, emit: Any, report_error: Exception | None
    ) -> None:
        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            return SUCCESS

        model = ScriptedModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[tool_response()]),
                    AIMessage(
                        content="", tool_calls=[tool_response(BandTool.NO_REPLY)]
                    ),
                ]
            ),
            disable_streaming=True,
        )
        adapter = LangGraphAdapter(
            llm=model, additional_tools=[(CustomDeclineInput, decline)], emit=emit
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        tools.send_event_error = report_error
        await adapter.on_event(turn_input(tools))
        assert model.requests == 1
        assert tools.turn.replied
