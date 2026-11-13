"""Gemini hands a raising custom tool's failure back to the model."""

from __future__ import annotations

import pytest
from google import genai

from band.adapters.gemini import GeminiAdapter, GeminiAdapterConfig
from band.core.exceptions import BandError
from band.runtime.tools import BandTool
from band.runtime.tools.schema import tool_execution_failure
from band.testing import FakeAgentTools, reported_failures
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input
from tests.hostcontrol import host_control_errors
from tests.integrations.gemini.peer import (
    WORK,
    WORK_FAILURE,
    GeneratePeer,
    WorkInput,
    call,
    tool_results,
)


class TestCustomToolFailure:
    async def test_raised_failure_reaches_the_model_and_the_turn_continues(
        self, gemini_peer: tuple[GeneratePeer, genai.Client]
    ) -> None:
        peer, client = gemini_peer

        async def work(inp: WorkInput) -> str:
            raise ValueError(WORK_FAILURE)

        adapter = GeminiAdapter(
            GeminiAdapterConfig(provider_key="test"),
            additional_tools=[(WorkInput, work)],
        )
        adapter.client = client
        await adapter.on_started("Bot", "Bot")
        peer.responses.extend(
            [[call(WORK, arguments={}, call_id="work")], [call(BandTool.NO_REPLY)]]
        )
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        assert tool_results(peer.requests[1]) == [
            {
                "id": "work",
                "name": WORK,
                "response": {
                    "error": tool_execution_failure(WORK, ValueError(WORK_FAILURE))
                },
            }
        ]
        assert len(peer.requests) == 2
        assert tools.turn.replied
        assert reported_failures(tools) == []

    @pytest.mark.parametrize("control", host_control_errors(), ids=type)
    async def test_host_control_is_not_returned_to_the_model(
        self, gemini_peer: tuple[GeneratePeer, genai.Client], control: BandError
    ) -> None:
        peer, client = gemini_peer

        async def work(inp: WorkInput) -> str:
            raise control

        adapter = GeminiAdapter(
            GeminiAdapterConfig(provider_key="test"),
            additional_tools=[(WorkInput, work)],
        )
        adapter.client = client
        await adapter.on_started("Bot", "Bot")
        peer.responses.append([call(WORK, arguments={}, call_id="work")])
        tools = FakeAgentTools(room_id=ROOM_ID)

        with pytest.raises(type(control)) as caught:
            await adapter.on_event(turn_input(tools))

        assert caught.value is control
        assert not tools.turn.complete
        assert len(peer.requests) == 1
