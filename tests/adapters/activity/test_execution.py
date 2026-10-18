from __future__ import annotations

import asyncio

import pytest
from google.genai import types

from band.adapters.anthropic import AnthropicAdapter
from band.adapters.gemini import GeminiAdapter
from band.runtime.custom_tools import get_custom_tool_name
from band.testing.activity import ActivityRecorder
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.activity.kit import ActivityLookup, anthropic_tool_response


class TestExecutedToolActivity:
    @pytest.mark.parametrize("provider", ["anthropic", "gemini"])
    @pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
    async def test_real_custom_execution_reports_names_only_and_always_ends(
        self, provider: str, outcome: str
    ) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        recorder = ActivityRecorder()
        tools = FakeAgentTools()
        tools._activity_observer = recorder
        name = get_custom_tool_name(ActivityLookup)

        async def lookup(args: ActivityLookup) -> str:
            assert args.note == "private-argument"
            entered.set()
            await release.wait()
            if outcome == "failure":
                raise ValueError("private-result")
            return "private-result"

        tool = (ActivityLookup, lookup)
        match provider:
            case "anthropic":
                adapter = AnthropicAdapter(additional_tools=[tool], emit=())
                operation = adapter._process_tool_calls(
                    anthropic_tool_response(name, "private-argument"), tools
                )
            case "gemini":
                adapter = GeminiAdapter(additional_tools=[tool], emit=())
                operation = adapter._process_function_calls(
                    [
                        types.FunctionCall(
                            id="call", name=name, args={"note": "private-argument"}
                        )
                    ],
                    tools,
                )
        task = asyncio.create_task(operation)
        await entered.wait()
        assert recorder.events == [{"kind": "tool_call", "name": name}]
        if outcome == "cancelled":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            await task
        recorder.assert_completed_tool(name)
        assert not tools.events_sent
