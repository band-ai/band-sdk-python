"""CrewAI wrappers retain failure identity, error events and lifecycle control."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from crewai.tools import ToolFailure

from band.core.exceptions import InputNotAccepted
from band.core.types import AdapterFeatures, Emit
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
from tests.adapters.crewaiturnend.kit import DECLINED_RESULT, DeclineInput
from tests.integrations.crewai.reporting import failed_tool_events


class TestFailureBoundary:
    @pytest.mark.parametrize(
        "raw,features,transport_error,expected_events",
        [
            case
            for raw in [{"ok": False, "reason": "denied"}, "Error: denied"]
            for case in [
                pytest.param(
                    raw,
                    AdapterFeatures(emit=frozenset()),
                    None,
                    [],
                    id=f"{type(raw).__name__}-disabled",
                ),
                pytest.param(
                    raw,
                    AdapterFeatures(emit=frozenset()),
                    RuntimeError("offline"),
                    [],
                    id=f"{type(raw).__name__}-disabled-offline",
                ),
                pytest.param(
                    raw,
                    AdapterFeatures(emit=frozenset({Emit.TOOL_CALLS})),
                    RuntimeError("offline"),
                    [],
                    id=f"{type(raw).__name__}-delivery-failed",
                ),
                pytest.param(
                    raw,
                    AdapterFeatures(emit=frozenset({Emit.TOOL_CALLS})),
                    None,
                    failed_tool_events(raw),
                    id=f"{type(raw).__name__}-delivered",
                ),
            ]
        ],
    )
    def test_failed_output_keeps_native_failure_and_one_truthful_result(
        self,
        raw: Any,
        features: AdapterFeatures,
        transport_error: Exception | None,
        expected_events: list[dict[str, Any]],
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
            reporter=EmitToolCallsReporter(features),
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
        assert tools.events_sent == expected_events

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
            return DECLINED_RESULT

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
