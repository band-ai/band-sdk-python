"""An Anthropic custom tool shows its name while it runs, even with narration off."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx2
from anthropic import AsyncAnthropic

from band.adapters.anthropic import AnthropicAdapter
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools.types import TurnEffect
from band.testing.activity import ActivityRecorder, shown_tool
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.turnprobes import turn_input
from tests.integrations.activitykit import LookupInput


def message(content: list[dict[str, Any]], stop_reason: str) -> dict[str, Any]:
    """A Messages API response body."""
    return {
        "id": "msg",
        "type": "message",
        "role": "assistant",
        "model": "scripted",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


def scripted_client(bodies: list[dict[str, Any]]) -> AsyncAnthropic:
    """A real client whose transport answers each request with the next body."""
    replies: Iterator[dict[str, Any]] = iter(bodies)

    def answer(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=next(replies))

    return AsyncAnthropic(
        api_key="test",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(answer)),
    )


class TestAnthropicCustomToolActivity:
    async def test_a_custom_tool_shows_its_name_until_it_returns(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        # Real work completes the turn, so it owes no reply.
        @declares_turn_effect(TurnEffect.ACT)
        async def lookup(inp: LookupInput) -> str:
            entered.set()
            await release.wait()
            return f"{inp.note}: a definition"

        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())
        adapter = AnthropicAdapter(additional_tools=[(LookupInput, lookup)], emit=())
        adapter.client = scripted_client(
            [
                message(
                    [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "lookup",
                            "input": {"note": "activity"},
                        }
                    ],
                    "tool_use",
                ),
                message([{"type": "text", "text": "Done."}], "end_turn"),
            ]
        )
        await adapter.on_started("Agent", "A test agent")

        turn = asyncio.create_task(adapter.on_event(turn_input(tools)))
        try:
            await entered.wait()
            assert recorder.events == [shown_tool("lookup")]
        finally:
            release.set()
            await turn

        recorder.assert_completed_tool("lookup")
