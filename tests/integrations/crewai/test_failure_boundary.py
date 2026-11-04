"""CrewAI wrappers retain failure identity, error events and lifecycle control."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from crewai.tools import ToolFailure

from band.core.exceptions import InputNotAccepted
from band.core.types import AdapterFeatures, Emit, ToolEventKey
from band.integrations.crewai.tools import (
    CrewAIToolContext,
    EmitToolCallsReporter,
    NoopReporter,
    ReplyTracker,
    build_band_crewai_tools,
)
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TurnEffect
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.crewaiturnend.kit import DeclineInput


class TestFailureBoundary:
    @pytest.mark.parametrize(
        "raw", [{"ok": False, "reason": "denied"}, "Error: denied"]
    )
    @pytest.mark.parametrize("emit", [True, False])
    @pytest.mark.parametrize("transport_error", [None, RuntimeError("offline")])
    def test_failed_output_keeps_native_failure_and_one_truthful_result(
        self, raw: Any, emit: bool, transport_error: Any
    ) -> None:
        tools = FakeAgentTools()
        tools.send_event_error = transport_error
        tracker = ReplyTracker()
        context = CrewAIToolContext("room", tools, tracker)

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(args: DeclineInput) -> Any:
            return raw

        wrapped = build_band_crewai_tools(
            get_context=lambda: context,
            reporter=EmitToolCallsReporter(
                AdapterFeatures(
                    emit=frozenset({Emit.TOOL_CALLS}) if emit else frozenset()
                )
            ),
            custom_tools=[(DeclineInput, decline)],
        )[-1]
        result = wrapped._run(reason="try")

        assert isinstance(result, ToolFailure)
        assert json.loads(result.as_agent_message()) == {
            "status": "error",
            "result": raw,
        }
        assert tracker.any_tool_ran
        assert tracker.posts == []
        assert not tools.turn.complete
        expected = [
            {
                "id": "evt-0",
                "content": json.dumps(
                    {ToolEventKey.NAME: "decline", ToolEventKey.ARGS: {"reason": "try"}}
                ),
                "message_type": "tool_call",
                "metadata": {},
            },
            {
                "id": "evt-1",
                "content": json.dumps(
                    {
                        ToolEventKey.NAME: "decline",
                        ToolEventKey.OUTPUT: raw,
                        ToolEventKey.IS_ERROR: True,
                    }
                ),
                "message_type": "tool_result",
                "metadata": {},
            },
        ]
        assert tools.events_sent == (
            expected if emit and transport_error is None else []
        )

    @pytest.mark.parametrize(
        "error", [InputNotAccepted("closing"), asyncio.CancelledError()]
    )
    def test_lifecycle_control_is_not_a_tool_failure(self, error: Any) -> None:
        tools = FakeAgentTools()
        context = CrewAIToolContext("room", tools)

        async def decline(args: DeclineInput) -> Any:
            raise error

        wrapped = build_band_crewai_tools(
            get_context=lambda: context,
            reporter=NoopReporter(),
            custom_tools=[(DeclineInput, decline)],
        )[-1]
        with pytest.raises(type(error)) as raised:
            wrapped._run(reason="try")
        assert raised.value is error
        assert not tools.turn.complete

    def test_missing_context_is_a_native_failure_without_invoking_the_handler(
        self,
    ) -> None:
        calls = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(args: DeclineInput) -> Any:
            calls.append(args.reason)
            return "declined"

        wrapped = build_band_crewai_tools(
            get_context=lambda: None,
            reporter=NoopReporter(),
            custom_tools=[(DeclineInput, decline)],
        )[-1]
        result = wrapped._run(reason="try")
        assert isinstance(result, ToolFailure)
        assert json.loads(result.as_agent_message()) == {
            "status": "error",
            "message": "No room context available - tool called outside message handling",
        }
        assert calls == []
