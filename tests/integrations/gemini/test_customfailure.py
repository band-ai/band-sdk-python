"""Gemini hands a raising custom tool's failure back to the model."""

from __future__ import annotations

from google import genai

from band.adapters.gemini import GeminiAdapter, GeminiAdapterConfig
from band.runtime.tools import BandTool
from band.runtime.tools.schema import tool_execution_failure
from band.testing import FakeAgentTools, reported_failures
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input
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
