"""Tests for tool_call and tool_result inclusion."""

from __future__ import annotations

import json

from band.converters.copilot_sdk import CopilotSDKHistoryConverter


class TestToolEventHandling:
    def test_includes_tool_call_as_raw_json(self):
        """tool_call messages are included as raw JSON."""
        converter = CopilotSDKHistoryConverter(agent_name="MyAgent")
        tool_call_json = json.dumps(
            {
                "name": "get_weather",
                "args": {"city": "NYC"},
                "tool_call_id": "call_123",
            }
        )
        raw = [
            {
                "role": "user",
                "content": "Hello",
                "sender_name": "User",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": tool_call_json,
                "message_type": "tool_call",
            },
        ]

        result = converter.convert(raw)

        assert "[User]: Hello" in result.text
        assert tool_call_json in result.text

    def test_includes_tool_result_as_raw_json(self):
        """tool_result messages are included as raw JSON."""
        converter = CopilotSDKHistoryConverter(agent_name="MyAgent")
        tool_result_json = json.dumps(
            {"output": "sunny, 72F", "tool_call_id": "call_123"}
        )
        raw = [
            {
                "role": "assistant",
                "content": tool_result_json,
                "message_type": "tool_result",
            }
        ]

        result = converter.convert(raw)

        assert result.text == tool_result_json

    def test_tool_events_included_regardless_of_agent_name(self):
        """Tool events are included as raw JSON regardless of agent_name."""
        converter = CopilotSDKHistoryConverter(agent_name="WeatherBot")
        tool_call_json = json.dumps({"name": "search", "args": {}})
        raw = [
            {
                "content": tool_call_json,
                "message_type": "tool_call",
            },
        ]

        result = converter.convert(raw)

        assert result.text == tool_call_json

    def test_skips_empty_tool_call_content(self):
        """Should skip tool_call with empty content."""
        converter = CopilotSDKHistoryConverter()
        raw = [
            {
                "content": "",
                "message_type": "tool_call",
            },
        ]

        result = converter.convert(raw)

        assert result.text == ""

    def test_skips_empty_tool_result_content(self):
        """Should skip tool_result with empty content."""
        converter = CopilotSDKHistoryConverter()
        raw = [
            {
                "content": "",
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert result.text == ""
