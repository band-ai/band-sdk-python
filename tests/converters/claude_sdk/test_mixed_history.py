"""Integration tests with mixed message types."""

from __future__ import annotations

import json

from band.converters.claude_sdk import (
    ClaudeSDKHistoryConverter,
)


class TestMixedHistory:
    def test_full_conversation_flow(self):
        """Should handle a realistic conversation with mixed message types."""
        converter = ClaudeSDKHistoryConverter(agent_name="WeatherAgent")
        tool_call_json = json.dumps(
            {
                "name": "get_weather",
                "args": {"city": "NYC"},
                "tool_call_id": "toolu_123",
            }
        )
        tool_result_json = json.dumps(
            {"output": "sunny, 72F", "tool_call_id": "toolu_123"}
        )
        raw = [
            # User asks question
            {
                "role": "user",
                "content": "What's the weather?",
                "sender_name": "Alice",
                "message_type": "text",
            },
            # Agent uses tool
            {
                "role": "assistant",
                "content": tool_call_json,
                "message_type": "tool_call",
            },
            # Tool result
            {
                "role": "assistant",
                "content": tool_result_json,
                "message_type": "tool_result",
            },
            # Agent responds with text (skipped - redundant)
            {
                "role": "assistant",
                "content": "It's sunny today!",
                "sender_name": "WeatherAgent",
                "message_type": "text",
            },
            # User follow-up
            {
                "role": "user",
                "content": "Thanks!",
                "sender_name": "Alice",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        # Should include user messages
        assert "[Alice]: What's the weather?" in result.text
        assert "[Alice]: Thanks!" in result.text
        # Should include raw tool event JSON
        assert tool_call_json in result.text
        assert tool_result_json in result.text
        # Should NOT include own text message
        assert "It's sunny today!" not in result.text

    def test_multi_agent_conversation(self):
        """Handles multiple agents in conversation."""
        converter = ClaudeSDKHistoryConverter(agent_name="MyAgent")
        raw = [
            {
                "role": "user",
                "content": "Hi team!",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": "Hello!",
                "sender_name": "OtherAgent",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": "Hi Alice!",
                "sender_name": "MyAgent",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        assert "[Alice]: Hi team!" in result.text
        assert "[OtherAgent]: Hello!" in result.text
        # Own message skipped
        assert "Hi Alice!" not in result.text

    def test_preserves_message_order(self):
        """Messages appear in the same order as input."""
        converter = ClaudeSDKHistoryConverter()
        raw = [
            {"content": "First", "sender_name": "A", "message_type": "text"},
            {"content": "Second", "sender_name": "B", "message_type": "text"},
            {"content": "Third", "sender_name": "C", "message_type": "text"},
        ]

        result = converter.convert(raw)
        lines = result.text.split("\n")

        assert len(lines) == 3
        assert "First" in lines[0]
        assert "Second" in lines[1]
        assert "Third" in lines[2]
