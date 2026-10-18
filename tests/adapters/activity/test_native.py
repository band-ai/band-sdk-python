from __future__ import annotations

import pytest
from claude_agent_sdk import ToolResultBlock, ToolUseBlock
from google.adk.events import Event
from google.genai import types

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.adapters.google_adk import GoogleADKAdapter
from band.adapters.langgraph import LangGraphAdapter
from band.testing.activity import ActivityRecorder
from band.testing.fake_tools import FakeAgentTools


class TestNativeActivity:
    @pytest.mark.parametrize("provider", ["claude", "langgraph", "adk"])
    async def test_start_and_end_are_independent_of_narration(
        self, provider: str
    ) -> None:
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        tools._activity_observer = recorder
        match provider:
            case "claude":
                adapter = ClaudeSDKAdapter(emit=())
                pending: dict[str, str] = {}
                await adapter._on_tool_use(
                    ToolUseBlock(
                        id="call", name="Bash", input={"command": "private-command"}
                    ),
                    pending,
                    "room",
                    tools,
                )
                await adapter._on_tool_result(
                    ToolResultBlock(tool_use_id="call", content="private-result"),
                    pending,
                    "room",
                    tools,
                )
            case "langgraph":
                adapter = LangGraphAdapter(graph_factory=lambda tools: None, emit=())
                await adapter._handle_stream_event(
                    {
                        "event": "on_tool_start",
                        "name": "Bash",
                        "data": {"input": "private-command"},
                    },
                    "room",
                    tools,
                )
                await adapter._handle_stream_event(
                    {
                        "event": "on_tool_error",
                        "name": "Bash",
                        "data": {"error": "private-result"},
                    },
                    "room",
                    tools,
                )
            case "adk":
                adapter = GoogleADKAdapter(emit=())
                await adapter._report_event(
                    Event(
                        author="test",
                        content=types.Content(
                            parts=[
                                types.Part(
                                    function_call=types.FunctionCall(
                                        name="Bash", args={"command": "private-command"}
                                    )
                                )
                            ]
                        ),
                    ),
                    tools,
                )
                await adapter._report_event(
                    Event(
                        author="test",
                        content=types.Content(
                            parts=[
                                types.Part(
                                    function_response=types.FunctionResponse(
                                        name="Bash",
                                        response={"output": "private-result"},
                                    )
                                )
                            ]
                        ),
                    ),
                    tools,
                )
        recorder.assert_completed_tool("Bash")
        assert not tools.events_sent
