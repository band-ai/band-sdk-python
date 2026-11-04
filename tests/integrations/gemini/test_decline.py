"""Gemini stops only after a complete successful declared decline batch."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest
from google import genai
from pydantic import create_model

from band.adapters.gemini import GeminiAdapter, GeminiAdapterConfig
from band.core.types import Emit, ToolEventKey
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import BandTool, TurnEffect
from band.testing import FakeAgentTools, events_of_type, reported_failures
from tests.adapters.usage_events import recorded_usage_payloads
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input, user_message
from tests.integrations.gemini.peer import (
    CUSTOM,
    FAILURE,
    SUCCESS,
    WORK,
    CustomDeclineInput,
    GeneratePeer,
    WorkInput,
    call,
    tool_results,
)

SIBLING_SUCCESS = {"id": "work", "name": WORK, "response": {"output": SUCCESS}}
SIBLING_FAILURE = {"id": "work", "name": WORK, "response": {"error": '{"ok": false}'}}


class TestDeclaredDecline:
    @pytest.mark.parametrize(
        ("output", "expected_requests", "serialized", "failed", "expected_response"),
        [
            (SUCCESS, 1, SUCCESS, False, {"output": SUCCESS}),
            ({"ok": False}, 2, '{"ok": false}', True, {"error": '{"ok": false}'}),
            (
                "Error: declined",
                2,
                "Error: declined",
                True,
                {"error": "Error: declined"},
            ),
        ],
    )
    async def test_result_identity_controls_the_next_request(
        self,
        gemini_peer: tuple[GeneratePeer, genai.Client],
        output: Any,
        expected_requests: int,
        serialized: str,
        failed: bool,
        expected_response: dict[str, str],
    ) -> None:
        peer, client = gemini_peer
        executed = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> Any:
            executed.append(inp.reason)
            return output

        adapter = GeminiAdapter(
            GeminiAdapterConfig(provider_key="test"),
            additional_tools=[(CustomDeclineInput, decline)],
            emit=(Emit.TOOL_CALLS, Emit.USAGE),
        )
        adapter.client = client
        await adapter.on_started("Bot", "Bot")
        peer.responses.extend(
            [[call()], [call(BandTool.NO_REPLY)], [call(BandTool.NO_REPLY)]]
        )
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        assert len(peer.requests) == expected_requests
        assert executed == ["FYI"]
        assert recorded_usage_payloads(tools) == [
            {
                "input_tokens": 7 * len(peer.requests),
                "output_tokens": 3 * len(peer.requests),
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
        ]
        assert reported_failures(tools) == []
        results = events_of_type(tools, "tool_result")
        assert json.loads(results[0]["content"]) == {
            ToolEventKey.NAME: CUSTOM,
            ToolEventKey.OUTPUT: serialized,
            ToolEventKey.TOOL_CALL_ID: "decline",
            ToolEventKey.IS_ERROR: failed,
        }
        next_tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(
            replace(turn_input(next_tools), is_session_bootstrap=False)
        )
        assert tool_results(peer.requests[1]) == [
            {
                "id": "decline",
                "name": CUSTOM,
                "response": expected_response,
            }
        ]

    @pytest.mark.parametrize(
        ("effect", "expected_requests"),
        [
            (TurnEffect.DECLINE, 1),
            (TurnEffect.OBSERVE, 2),
            (TurnEffect.ACT, 2),
            (TurnEffect.REPLY, 2),
        ],
    )
    async def test_other_effects_continue(
        self,
        gemini_peer: tuple[GeneratePeer, genai.Client],
        effect: TurnEffect,
        expected_requests: int,
    ) -> None:
        peer, client = gemini_peer

        @declares_turn_effect(effect)
        async def decline(inp: CustomDeclineInput) -> str:
            return SUCCESS

        adapter = GeminiAdapter(
            GeminiAdapterConfig(provider_key="test"),
            additional_tools=[(CustomDeclineInput, decline)],
        )
        adapter.client = client
        await adapter.on_started("Bot", "Bot")
        peer.responses.extend([[call()], [call(BandTool.NO_REPLY)], [{"text": "done"}]])
        await adapter.on_event(turn_input(FakeAgentTools(room_id=ROOM_ID)))
        assert len(peer.requests) == expected_requests

    @pytest.mark.parametrize(
        ("arguments", "expected_executed"),
        [({"reason": 42}, []), ({"reason": FAILURE}, [FAILURE])],
    )
    async def test_invalid_or_raised_decline_recovers(
        self,
        gemini_peer: tuple[GeneratePeer, genai.Client],
        arguments: dict[str, Any],
        expected_executed: list[str],
    ) -> None:
        peer, client = gemini_peer
        executed = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            executed.append(inp.reason)
            raise ValueError(FAILURE)

        adapter = GeminiAdapter(
            GeminiAdapterConfig(provider_key="test"),
            additional_tools=[(CustomDeclineInput, decline)],
        )
        adapter.client = client
        await adapter.on_started("Bot", "Bot")
        peer.responses.extend([[call(arguments=arguments)], [call(BandTool.NO_REPLY)]])
        await adapter.on_event(turn_input(FakeAgentTools(room_id=ROOM_ID)))
        assert len(peer.requests) == 2
        assert executed == expected_executed
        assert (
            "error"
            in peer.requests[1]["contents"][2]["parts"][0]["functionResponse"][
                "response"
            ]
        )

    @pytest.mark.parametrize(
        ("sibling_output", "expected_requests", "expected_result"),
        [(SUCCESS, 1, SIBLING_SUCCESS), ({"ok": False}, 2, SIBLING_FAILURE)],
    )
    async def test_batch_keeps_all_results_and_failed_sibling_recovery(
        self,
        gemini_peer: tuple[GeneratePeer, genai.Client],
        sibling_output: Any,
        expected_requests: int,
        expected_result: dict[str, Any],
    ) -> None:
        peer, client = gemini_peer
        executed = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            executed.append(CUSTOM)
            return SUCCESS

        async def work(inp: WorkInput) -> Any:
            executed.append(WORK)
            return sibling_output

        adapter = GeminiAdapter(
            GeminiAdapterConfig(provider_key="test"),
            additional_tools=[(CustomDeclineInput, decline), (WorkInput, work)],
        )
        adapter.client = client
        await adapter.on_started("Bot", "Bot")
        peer.responses.extend(
            [
                [call(), call(WORK, arguments={}, call_id="work")],
                [call(BandTool.NO_REPLY)],
                [call(BandTool.NO_REPLY)],
            ]
        )
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        assert executed == [CUSTOM, WORK]
        assert len(peer.requests) == expected_requests
        assert reported_failures(tools) == []
        next_tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(
            replace(turn_input(next_tools), is_session_bootstrap=False)
        )
        assert tool_results(peer.requests[1]) == [
            {"id": "decline", "name": CUSTOM, "response": {"output": SUCCESS}},
            expected_result,
        ]

    async def test_custom_winner_does_not_inherit_platform_decline(
        self, gemini_peer: tuple[GeneratePeer, genai.Client]
    ) -> None:
        peer, client = gemini_peer
        shadow_input = create_model(f"{BandTool.NO_REPLY}Input", reason=(str, ...))
        executed = []

        async def shadow(inp: Any) -> str:
            executed.append("shadow")
            return SUCCESS

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            executed.append(CUSTOM)
            return SUCCESS

        adapter = GeminiAdapter(
            GeminiAdapterConfig(provider_key="test"),
            additional_tools=[(shadow_input, shadow), (CustomDeclineInput, decline)],
        )
        adapter.client = client
        await adapter.on_started("Bot", "Bot")
        peer.responses.extend([[call(BandTool.NO_REPLY)], [call()]])
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        assert len(peer.requests) == 2
        assert executed == ["shadow", CUSTOM]
        assert reported_failures(tools) == []

    async def test_retained_adapter_accepts_new_input_after_decline(
        self, gemini_peer: tuple[GeneratePeer, genai.Client]
    ) -> None:
        peer, client = gemini_peer
        executed = []

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            executed.append(inp.reason)
            return SUCCESS

        adapter = GeminiAdapter(
            GeminiAdapterConfig(provider_key="test"),
            additional_tools=[(CustomDeclineInput, decline)],
        )
        adapter.client = client
        await adapter.on_started("Bot", "Bot")
        peer.responses.extend([[call()], [call(BandTool.NO_REPLY)], [{"text": "done"}]])
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        next_tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(
            replace(turn_input(next_tools), is_session_bootstrap=False)
        )
        assert len(peer.requests) == 2
        assert executed == ["FYI"]
        assert next_tools.turn.replied
        assert reported_failures(next_tools) == []

    async def test_declining_room_does_not_cancel_gated_room(
        self, gemini_peer: tuple[GeneratePeer, genai.Client]
    ) -> None:
        peer, client = gemini_peer
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            entered.set()
            try:
                await release.wait()
                return SUCCESS
            finally:
                finished.set()

        adapter = GeminiAdapter(
            GeminiAdapterConfig(provider_key="test"),
            additional_tools=[(CustomDeclineInput, decline)],
        )
        adapter.client = client
        await adapter.on_started("Bot", "Bot")
        peer.responses.extend([[call()], [call(BandTool.NO_REPLY)], [{"text": "done"}]])
        slow_tools = FakeAgentTools(room_id="slow")
        fast_tools = FakeAgentTools(room_id="fast")
        async with asyncio.TaskGroup() as group:
            task = group.create_task(
                adapter.on_event(
                    turn_input(slow_tools, replace(user_message(), room_id="slow"))
                )
            )
            try:
                await entered.wait()
                await adapter.on_event(
                    turn_input(fast_tools, replace(user_message(), room_id="fast"))
                )
                assert not task.done()
                assert not finished.is_set()
            finally:
                release.set()
        assert len(peer.requests) == 2
        assert finished.is_set()
        assert slow_tools.turn.replied and fast_tools.turn.replied
        assert reported_failures(slow_tools) == reported_failures(fast_tools) == []

    async def test_cancellation_preserves_identity_and_handler_cleanup(
        self, gemini_peer: tuple[GeneratePeer, genai.Client]
    ) -> None:
        peer, client = gemini_peer
        entered, finished = asyncio.Event(), asyncio.Event()

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            entered.set()
            try:
                await asyncio.Event().wait()
                return SUCCESS
            finally:
                finished.set()

        adapter = GeminiAdapter(
            GeminiAdapterConfig(provider_key="test"),
            additional_tools=[(CustomDeclineInput, decline)],
        )
        adapter.client = client
        await adapter.on_started("Bot", "Bot")
        peer.responses.append([call()])
        tools = FakeAgentTools(room_id=ROOM_ID)
        task = asyncio.create_task(adapter.on_event(turn_input(tools)))
        try:
            await entered.wait()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert len(peer.requests) == 1
        assert finished.is_set()
        assert not tools.turn.replied
        assert reported_failures(tools) == []

    @pytest.mark.parametrize("emit", [(), Emit.TOOL_CALLS])
    @pytest.mark.parametrize("report_error", [None, RuntimeError("report unavailable")])
    async def test_reporting_cannot_change_successful_decline(
        self,
        gemini_peer: tuple[GeneratePeer, genai.Client],
        emit: Any,
        report_error: Exception | None,
    ) -> None:
        peer, client = gemini_peer

        @declares_turn_effect(TurnEffect.DECLINE)
        async def decline(inp: CustomDeclineInput) -> str:
            return SUCCESS

        adapter = GeminiAdapter(
            GeminiAdapterConfig(provider_key="test"),
            additional_tools=[(CustomDeclineInput, decline)],
            emit=emit,
        )
        adapter.client = client
        await adapter.on_started("Bot", "Bot")
        peer.responses.extend([[call()], [call(BandTool.NO_REPLY)]])
        tools = FakeAgentTools(room_id=ROOM_ID)
        tools.send_event_error = report_error
        await adapter.on_event(turn_input(tools))
        assert len(peer.requests) == 1
        assert tools.turn.replied
