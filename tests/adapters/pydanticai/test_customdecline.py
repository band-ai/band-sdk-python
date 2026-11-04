"""Portable and native successful declines stop the current Pydantic run."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from pydantic_ai import AgentRunResultEvent, ModelRetry, ToolFailed
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import RetryPromptPart, ToolReturnPart
from pydantic_ai.usage import UsageLimits

from band.adapters.pydantic_ai import PydanticAIAdapter
from band.core.types import Emit, ToolEventKey
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import BandTool, TurnEffect
from band.testing import FakeAgentTools, events_of_type, reported_failures
from tests.adapters.pydanticai.declinepeer import (
    CUSTOM,
    FAILURE,
    SUCCESS,
    CustomDeclineInput,
    ScriptedModel,
    native_registration,
    portable_registration,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input


class TestCustomDecline:
    @pytest.mark.parametrize("register", [portable_registration, native_registration])
    @pytest.mark.parametrize("request_limit", [1, 3])
    async def test_success_stops_before_request_limit(
        self, register: Callable[..., Any], request_limit: int
    ) -> None:
        calls = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            calls.append(inp.reason)
            return SUCCESS

        model = ScriptedModel([(CUSTOM, {"reason": "FYI"})], [(BandTool.NO_REPLY, {})])
        adapter = PydanticAIAdapter(
            llm=model.model, additional_tools=[register(decline)]
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        results = []
        async with adapter._agent.run_stream_events(
            "go", deps=tools, usage_limits=UsageLimits(request_limit=request_limit)
        ) as events:
            async for event in events:
                if isinstance(event, AgentRunResultEvent):
                    results.append(event.result)
        assert len(model.requests) == 1
        assert calls == ["FYI"]
        assert results[0].output is None
        assert [
            (part.tool_name, part.content, part.outcome)
            for message in results[0].all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ] == [(CUSTOM, SUCCESS, "success")]

    @pytest.mark.parametrize("register", [portable_registration, native_registration])
    @pytest.mark.parametrize("output", [{"ok": False}, "Error: declined"])
    async def test_returned_failure_reaches_next_model_and_error_event(
        self, register: Callable[..., Any], output: Any
    ) -> None:
        calls = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> Any:
            calls.append(inp.reason)
            return output

        model = ScriptedModel([(CUSTOM, {"reason": "FYI"})], [(BandTool.NO_REPLY, {})])
        adapter = PydanticAIAdapter(
            llm=model.model, additional_tools=[register(decline)], emit=Emit.TOOL_CALLS
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        assert len(model.requests) == 2
        assert calls == ["FYI"]
        assert json.loads(events_of_type(tools, "tool_result")[0]["content"]) == {
            ToolEventKey.NAME: CUSTOM,
            ToolEventKey.OUTPUT: str(output),
            ToolEventKey.TOOL_CALL_ID: "call-1-0",
            ToolEventKey.IS_ERROR: True,
        }
        assert reported_failures(tools) == []

    @pytest.mark.parametrize("register", [portable_registration, native_registration])
    async def test_value_error_propagates_without_decline_or_retry(
        self, register: Callable[..., Any]
    ) -> None:
        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            raise ValueError(FAILURE)

        model = ScriptedModel([(CUSTOM, {"reason": "FYI"})], [(BandTool.NO_REPLY, {})])
        adapter = PydanticAIAdapter(
            llm=model.model, additional_tools=[register(decline)]
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        with pytest.raises(ValueError, match=FAILURE):
            await adapter.on_event(turn_input(tools))
        assert len(model.requests) == 1
        assert not tools.turn.replied

    @pytest.mark.parametrize("register", [portable_registration, native_registration])
    async def test_native_retry_allows_actual_recovery(
        self, register: Callable[..., Any]
    ) -> None:
        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            raise ModelRetry(FAILURE)

        model = ScriptedModel([(CUSTOM, {"reason": "FYI"})], [(BandTool.NO_REPLY, {})])
        adapter = PydanticAIAdapter(
            llm=model.model, additional_tools=[register(decline)]
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        assert len(model.requests) == 2
        assert [
            part.content
            for message in model.requests[1]
            for part in message.parts
            if isinstance(part, RetryPromptPart)
        ] == [FAILURE]
        assert reported_failures(tools) == []

    @pytest.mark.parametrize("register", [portable_registration, native_registration])
    async def test_adapter_records_success_and_retains_it_for_the_next_turn(
        self, register: Callable[..., Any]
    ) -> None:
        calls = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            calls.append(inp.reason)
            return SUCCESS

        model = ScriptedModel([(CUSTOM, {"reason": "FYI"})], [(BandTool.NO_REPLY, {})])
        adapter = PydanticAIAdapter(
            llm=model.model, additional_tools=[register(decline)], emit=Emit.TOOL_CALLS
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        assert tools.turn.replied
        assert json.loads(events_of_type(tools, "tool_result")[0]["content"]) == {
            ToolEventKey.NAME: CUSTOM,
            ToolEventKey.OUTPUT: SUCCESS,
            ToolEventKey.TOOL_CALL_ID: "call-1-0",
            ToolEventKey.IS_ERROR: False,
        }
        next_tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(
            replace(turn_input(next_tools), is_session_bootstrap=False)
        )
        assert len(model.requests) == 2
        assert calls == ["FYI"]
        assert next_tools.turn.replied
        assert [
            (part.tool_name, part.content)
            for message in model.requests[1]
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ] == [(CUSTOM, SUCCESS)]
        assert reported_failures(tools) == reported_failures(next_tools) == []

    @pytest.mark.parametrize("register", [portable_registration, native_registration])
    @pytest.mark.parametrize(
        "effect", [TurnEffect.OBSERVE, TurnEffect.ACT, TurnEffect.REPLY]
    )
    async def test_other_declared_effects_leave_the_next_step_executable(
        self, register: Callable[..., Any], effect: TurnEffect
    ) -> None:
        @declares_turn_effect(effect)
        async def decline(inp: CustomDeclineInput) -> str:
            return SUCCESS

        model = ScriptedModel([(CUSTOM, {"reason": "FYI"})], [(BandTool.NO_REPLY, {})])
        adapter = PydanticAIAdapter(
            llm=model.model, additional_tools=[register(decline)]
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        assert len(model.requests) == 2
        assert reported_failures(tools) == []

    @pytest.mark.parametrize("register", [portable_registration, native_registration])
    @pytest.mark.parametrize("sibling_output", [SUCCESS, {"ok": False}])
    async def test_all_sibling_results_precede_the_stop_decision(
        self, register: Callable[..., Any], sibling_output: Any
    ) -> None:
        completed = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            completed.append(CUSTOM)
            return SUCCESS

        async def work() -> Any:
            completed.append("work")
            return sibling_output

        model = ScriptedModel(
            [(CUSTOM, {"reason": "FYI"}), ("work", {})], [(BandTool.NO_REPLY, {})]
        )
        adapter = PydanticAIAdapter(
            llm=model.model, additional_tools=[register(decline), work]
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        assert sorted(completed) == sorted([CUSTOM, "work"])
        assert len(model.requests) == (1 if sibling_output == SUCCESS else 2)
        assert [
            (part.tool_name, part.content)
            for message in adapter._message_history[ROOM_ID]
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ][:2] == [(CUSTOM, SUCCESS), ("work", sibling_output)]
        assert reported_failures(tools) == []

    @pytest.mark.parametrize("register", [portable_registration, native_registration])
    async def test_native_failed_outcome_cannot_record_a_decline(
        self, register: Callable[..., Any]
    ) -> None:
        tools = FakeAgentTools(room_id=ROOM_ID)
        observed = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            raise ToolFailed(FAILURE)

        @declares_turn_effect(TurnEffect.ACT)
        async def recover() -> str:
            observed.append(tools.turn.replied)
            return SUCCESS

        model = ScriptedModel([(CUSTOM, {"reason": "FYI"})], [("recover", {})], [])
        adapter = PydanticAIAdapter(
            llm=model.model,
            additional_tools=[register(decline), recover],
            emit=Emit.TOOL_CALLS,
        )
        await adapter.on_started("Bot", "Bot")
        await adapter.on_event(turn_input(tools))
        assert len(model.requests) == 3
        assert observed == [False]
        assert json.loads(events_of_type(tools, "tool_result")[0]["content"]) == {
            ToolEventKey.NAME: CUSTOM,
            ToolEventKey.OUTPUT: FAILURE,
            ToolEventKey.TOOL_CALL_ID: "call-1-0",
            ToolEventKey.IS_ERROR: True,
        }
        assert [
            (part.tool_name, part.content, part.outcome)
            for message in model.requests[1]
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ] == [(CUSTOM, FAILURE, "failed")]
        assert reported_failures(tools) == []

    @pytest.mark.parametrize("register", [portable_registration, native_registration])
    async def test_invalid_input_never_executes_the_handler(
        self, register: Callable[..., Any]
    ) -> None:
        executed = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            executed.append(inp)
            return SUCCESS

        model = ScriptedModel([(CUSTOM, {"reason": 42})], [(BandTool.NO_REPLY, {})])
        adapter = PydanticAIAdapter(
            llm=model.model, additional_tools=[register(decline)]
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        assert len(model.requests) == 2
        assert executed == []
        assert reported_failures(tools) == []

    async def test_native_platform_name_collision_retains_rejection(self) -> None:
        async def band_no_reply(reason: str) -> str:
            raise AssertionError("conflicting custom handler executed")

        model = ScriptedModel([(BandTool.NO_REPLY, {"reason": "FYI"})])
        adapter = PydanticAIAdapter(llm=model.model, additional_tools=[band_no_reply])
        with pytest.raises(UserError, match="Tool name conflicts"):
            await adapter.on_started("Bot", "Bot")
            await adapter.on_event(turn_input(FakeAgentTools(room_id=ROOM_ID)))
        assert model.requests == []

    @pytest.mark.parametrize("register", [portable_registration, native_registration])
    @pytest.mark.parametrize("emit", [(), Emit.TOOL_CALLS])
    @pytest.mark.parametrize("report_error", [None, RuntimeError("report unavailable")])
    async def test_reporting_cannot_change_successful_decline(
        self, register: Callable[..., Any], emit: Any, report_error: Exception | None
    ) -> None:
        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            return SUCCESS

        model = ScriptedModel([(CUSTOM, {"reason": "FYI"})], [(BandTool.NO_REPLY, {})])
        adapter = PydanticAIAdapter(
            llm=model.model, additional_tools=[register(decline)], emit=emit
        )
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=ROOM_ID)
        tools.send_event_error = report_error
        await adapter.on_event(turn_input(tools))
        assert len(model.requests) == 1
        assert tools.turn.replied
