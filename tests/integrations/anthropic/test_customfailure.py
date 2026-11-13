"""Anthropic hands a raising custom tool's failure back to the model."""

from __future__ import annotations

from anthropic import AsyncAnthropic

from band.adapters.anthropic import AnthropicAdapter, AnthropicAdapterConfig
from band.runtime.tools import BandTool
from band.runtime.tools.schema import tool_execution_failure
from band.testing import FakeAgentTools, reported_failures
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input
from tests.integrations.anthropic.peer import (
    WORK,
    WORK_FAILURE,
    MessagesPeer,
    WorkInput,
    call,
    tool_results,
)


class TestCustomToolFailure:
    async def test_raised_failure_reaches_the_model_and_the_turn_continues(
        self, anthropic_peer: tuple[MessagesPeer, AsyncAnthropic]
    ) -> None:
        peer, client = anthropic_peer

        async def work(inp: WorkInput) -> str:
            raise ValueError(WORK_FAILURE)

        adapter = AnthropicAdapter(
            AnthropicAdapterConfig(provider_key="test"),
            additional_tools=[(WorkInput, work)],
        )
        await adapter.client.close()
        adapter.client = client
        await adapter.on_started("Bot", "Bot")
        peer.responses.extend(
            [
                [call(WORK, arguments={}, call_id="work")],
                [call(BandTool.NO_REPLY)],
            ]
        )
        tools = FakeAgentTools(room_id=ROOM_ID)
        await adapter.on_event(turn_input(tools))
        assert tool_results(peer.requests[1]) == [
            {
                "type": "tool_result",
                "tool_use_id": "work",
                "content": tool_execution_failure(WORK, ValueError(WORK_FAILURE)),
                "is_error": True,
            }
        ]
        assert len(peer.requests) == 2
        assert tools.turn.replied
        assert reported_failures(tools) == []
