"""Tests for ClaudeSDKHistoryConverter.

Tests for shared converter behavior (user messages, agent filtering, empty
history, edge cases, output shape) live in
tests/framework_conformance/test_converter_conformance.py.
This file contains ClaudeSDK-specific multi-message joining, tool event
handling, session ID extraction, and mixed history integration tests.
"""

from __future__ import annotations

import json

from band.converters.claude_sdk import (
    ClaudeSDKHistoryConverter,
    ClaudeSDKSessionState,
)


class TestBasicConversion:
    """Tests for ClaudeSDK-specific message conversion to text."""

    def test_converts_multiple_messages(self):
        """Multiple messages are joined with newlines."""
        converter = ClaudeSDKHistoryConverter()
        raw = [
            {
                "role": "user",
                "content": "Hello!",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "role": "user",
                "content": "Hi there!",
                "sender_name": "Bob",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        expected = "[Alice]: Hello!\n[Bob]: Hi there!"
        assert result.text == expected

    def test_returns_session_state(self):
        """convert() returns a ClaudeSDKSessionState instance."""
        converter = ClaudeSDKHistoryConverter()
        result = converter.convert([])
        assert isinstance(result, ClaudeSDKSessionState)


class TestEdgeCases:
    """ClaudeSDK-specific edge cases not covered by conformance tests.

    The conformance test ``test_defaults_to_user_role`` skips ClaudeSDK
    because ``has_role_concept=False`` (output is a flat string).  This
    class covers the missing-role behavior directly.
    """

    def test_missing_role_defaults_to_user(self):
        """Messages without a 'role' key are treated as user messages."""
        converter = ClaudeSDKHistoryConverter(agent_name="MyAgent")
        raw = [
            {
                "content": "Hello",
                "sender_name": "Bob",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        # Should be included (not filtered as own-agent message)
        assert "[Bob]: Hello" in result.text

    def test_missing_role_not_filtered_as_assistant(self):
        """Messages without 'role' must not be filtered by agent_name."""
        converter = ClaudeSDKHistoryConverter(agent_name="MyAgent")
        raw = [
            {
                "content": "I said something",
                "sender_name": "MyAgent",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        # Without "role": "assistant", the message defaults to user role
        # and should NOT be filtered even though sender_name matches agent_name.
        assert "I said something" in result.text


class TestToolEventHandling:
    """Tests for tool_call and tool_result inclusion."""

    def test_includes_tool_call_as_raw_json(self):
        """tool_call messages are included as raw JSON."""
        converter = ClaudeSDKHistoryConverter(agent_name="MyAgent")
        tool_call_json = json.dumps(
            {
                "name": "get_weather",
                "args": {"city": "NYC"},
                "tool_call_id": "toolu_123",
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
        # Raw JSON is included as-is
        assert tool_call_json in result.text

    def test_includes_tool_result_as_raw_json(self):
        """tool_result messages are included as raw JSON."""
        converter = ClaudeSDKHistoryConverter(agent_name="MyAgent")
        tool_result_json = json.dumps(
            {"output": "sunny, 72F", "tool_call_id": "toolu_123"}
        )
        raw = [
            {
                "role": "assistant",
                "content": tool_result_json,
                "message_type": "tool_result",
            }
        ]

        result = converter.convert(raw)

        # Raw JSON is included as-is
        assert result.text == tool_result_json

    def test_tool_events_included_regardless_of_agent_name(self):
        """Tool events are included as raw JSON regardless of agent_name."""
        converter = ClaudeSDKHistoryConverter(agent_name="WeatherBot")
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
        converter = ClaudeSDKHistoryConverter()
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
        converter = ClaudeSDKHistoryConverter()
        raw = [
            {
                "content": "",
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert result.text == ""


class TestSessionIdExtraction:
    """Tests for session_id extraction from task events."""

    def test_extracts_session_id_from_task_event(self):
        """Should extract session_id from task event metadata."""
        converter = ClaudeSDKHistoryConverter()
        raw = [
            {
                "role": "user",
                "content": "Hello",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "content": "Claude SDK session",
                "message_type": "task",
                "metadata": {"claude_sdk_session_id": "sess-abc-123"},
            },
        ]

        result = converter.convert(raw)

        assert result.session_id == "sess-abc-123"

    def test_returns_latest_session_id(self):
        """When multiple task events exist, return the latest session_id."""
        converter = ClaudeSDKHistoryConverter()
        raw = [
            {
                "content": "Claude SDK session",
                "message_type": "task",
                "metadata": {"claude_sdk_session_id": "sess-old"},
            },
            {
                "role": "user",
                "content": "Hello",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "content": "Claude SDK session",
                "message_type": "task",
                "metadata": {"claude_sdk_session_id": "sess-new"},
            },
        ]

        result = converter.convert(raw)

        assert result.session_id == "sess-new"

    def test_returns_none_when_no_task_events(self):
        """Should return None when no task events exist."""
        converter = ClaudeSDKHistoryConverter()
        raw = [
            {
                "role": "user",
                "content": "Hello",
                "sender_name": "Alice",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        assert result.session_id is None

    def test_ignores_task_events_without_session_id_key(self):
        """Should ignore task events that don't have claude_sdk_session_id."""
        converter = ClaudeSDKHistoryConverter()
        raw = [
            {
                "content": "Some other task event",
                "message_type": "task",
                "metadata": {"other_key": "value"},
            },
        ]

        result = converter.convert(raw)

        assert result.session_id is None

    def test_task_events_not_included_in_text(self):
        """Task events should never appear in the text output."""
        converter = ClaudeSDKHistoryConverter()
        raw = [
            {
                "role": "user",
                "content": "Hello",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "content": "Claude SDK session",
                "message_type": "task",
                "metadata": {"claude_sdk_session_id": "sess-abc"},
            },
        ]

        result = converter.convert(raw)

        assert "Claude SDK session" not in result.text
        assert "sess-abc" not in result.text
        assert result.text == "[Alice]: Hello"

    def test_empty_history_returns_empty_state(self):
        """Empty history should return empty state with no session_id."""
        converter = ClaudeSDKHistoryConverter()

        result = converter.convert([])

        assert result.text == ""
        assert result.session_id is None


class TestMixedHistory:
    """Integration tests with mixed message types."""

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
