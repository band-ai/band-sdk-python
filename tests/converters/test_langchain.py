"""Tests for LangChainHistoryConverter.

Tests for shared converter behavior (user messages, agent filtering, empty
history, edge cases) live in tests/framework_conformance/test_converter_conformance.py.
This file contains LangChain-specific tool call pairing, error handling, and
mixed history integration tests.
"""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from thenvoi.converters.langchain import LangChainHistoryConverter


class TestToolCallPairing:
    """Tests for tool_call + tool_result pairing."""

    def test_pairs_tool_call_with_tool_result(self):
        """Should create AIMessage with tool_calls and ToolMessage pair."""
        converter = LangChainHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_start",
                        "name": "thenvoi_send_message",
                        "run_id": "run-123",
                        "data": {"input": {"content": "Hello!", "mentions": []}},
                    }
                ),
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_end",
                        "name": "thenvoi_send_message",
                        "run_id": "run-456",
                        "data": {
                            "input": {},
                            "output": "content='OK' name='thenvoi_send_message' tool_call_id='call_abc123'",
                        },
                    }
                ),
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert len(result) == 2

        # First should be AIMessage with tool_calls
        assert isinstance(result[0], AIMessage)
        assert result[0].content == ""
        assert len(result[0].tool_calls) == 1
        assert result[0].tool_calls[0]["name"] == "thenvoi_send_message"
        assert result[0].tool_calls[0]["id"] == "call_abc123"
        assert result[0].tool_calls[0]["args"] == {"content": "Hello!", "mentions": []}

        # Second should be ToolMessage
        assert isinstance(result[1], ToolMessage)
        assert result[1].tool_call_id == "call_abc123"

    def test_pairs_tool_call_with_structured_tool_result(self):
        converter = LangChainHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_start",
                        "name": "thenvoi_send_message",
                        "run_id": "run-123",
                        "data": {"input": {"content": "Hello!", "mentions": []}},
                    }
                ),
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_end",
                        "name": "thenvoi_send_message",
                        "run_id": "run-456",
                        "data": {
                            "output": {
                                "content": "OK",
                                "tool_call_id": "call_structured",
                            },
                        },
                    }
                ),
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert result[0].tool_calls[0]["id"] == "call_structured"
        assert result[1].tool_call_id == "call_structured"

    def test_uses_run_id_when_tool_call_id_not_found(self):
        """Should fall back to run_id when tool_call_id not in output."""
        converter = LangChainHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_start",
                        "name": "search",
                        "run_id": "run-789",
                        "data": {"input": {"query": "test"}},
                    }
                ),
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_end",
                        "name": "search",
                        "run_id": "run-789",
                        "data": {
                            "input": {},
                            "output": "No results found",  # No tool_call_id in output
                        },
                    }
                ),
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        # Should use run_id as fallback
        assert result[0].tool_calls[0]["id"] == "run-789"
        assert result[1].tool_call_id == "run-789"

    def test_handles_multiple_tool_calls(self):
        """Should handle multiple sequential tool calls."""
        converter = LangChainHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_start",
                        "name": "tool_a",
                        "run_id": "run-1",
                        "data": {"input": {"a": 1}},
                    }
                ),
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_end",
                        "name": "tool_a",
                        "run_id": "run-1",
                        "data": {"output": "result_a tool_call_id='call_1'"},
                    }
                ),
                "message_type": "tool_result",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_start",
                        "name": "tool_b",
                        "run_id": "run-2",
                        "data": {"input": {"b": 2}},
                    }
                ),
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_end",
                        "name": "tool_b",
                        "run_id": "run-2",
                        "data": {"output": "result_b tool_call_id='call_2'"},
                    }
                ),
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert len(result) == 4
        assert result[0].tool_calls[0]["name"] == "tool_a"
        assert result[2].tool_calls[0]["name"] == "tool_b"

    def test_pairs_same_name_tool_calls_by_run_id(self):
        converter = LangChainHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_start",
                        "name": "lookup",
                        "run_id": "run-first",
                        "data": {"input": {"query": "first"}},
                    }
                ),
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_start",
                        "name": "lookup",
                        "run_id": "run-second",
                        "data": {"input": {"query": "second"}},
                    }
                ),
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_end",
                        "name": "lookup",
                        "run_id": "run-second",
                        "data": {"output": "second result tool_call_id='call_second'"},
                    }
                ),
                "message_type": "tool_result",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_end",
                        "name": "lookup",
                        "run_id": "run-first",
                        "data": {"output": "first result tool_call_id='call_first'"},
                    }
                ),
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert result[0].tool_calls[0]["id"] == "call_second"
        assert result[0].tool_calls[0]["args"] == {"query": "second"}
        assert result[2].tool_calls[0]["id"] == "call_first"
        assert result[2].tool_calls[0]["args"] == {"query": "first"}

    def test_pairs_same_name_tool_calls_in_call_order_by_run_id(self):
        converter = LangChainHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_start",
                        "name": "lookup",
                        "run_id": "run-first",
                        "data": {"input": {"query": "first"}},
                    }
                ),
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_start",
                        "name": "lookup",
                        "run_id": "run-second",
                        "data": {"input": {"query": "second"}},
                    }
                ),
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_end",
                        "name": "lookup",
                        "run_id": "run-first",
                        "data": {"output": "first result"},
                    }
                ),
                "message_type": "tool_result",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_end",
                        "name": "lookup",
                        "run_id": "run-second",
                        "data": {"output": "second result"},
                    }
                ),
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert result[0].tool_calls[0]["id"] == "run-first"
        assert result[0].tool_calls[0]["args"] == {"query": "first"}
        assert result[1].tool_call_id == "run-first"
        assert result[2].tool_calls[0]["id"] == "run-second"
        assert result[2].tool_calls[0]["args"] == {"query": "second"}
        assert result[3].tool_call_id == "run-second"

    def test_skips_ambiguous_same_name_tool_result_with_nonmatching_ids(self):
        converter = LangChainHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_start",
                        "name": "lookup",
                        "run_id": "run-first",
                        "data": {"input": {"query": "first"}},
                    }
                ),
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_start",
                        "name": "lookup",
                        "run_id": "run-second",
                        "data": {"input": {"query": "second"}},
                    }
                ),
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_end",
                        "name": "lookup",
                        "run_id": "run-missing",
                        "data": {"output": "missing tool_call_id='call_missing'"},
                    }
                ),
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert result == []

    def test_falls_back_to_lifo_by_name_without_tool_call_id_match(self):
        pending = [
            {"name": "lookup", "args": {"query": "first"}},
            {"name": "lookup", "args": {"query": "second"}},
        ]

        match = LangChainHistoryConverter._pop_matching_tool_call(
            pending,
            tool_name="lookup",
            match_ids=(),
        )

        assert match == {"name": "lookup", "args": {"query": "second"}}
        assert pending == [{"name": "lookup", "args": {"query": "first"}}]


class TestErrorHandling:
    """Tests for error handling."""

    def test_handles_malformed_tool_call_json(self):
        """Should handle malformed JSON in tool_call gracefully."""
        converter = LangChainHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": "not valid json",
                "message_type": "tool_call",
            }
        ]

        result = converter.convert(raw)

        # Should not crash, just skip the malformed entry
        assert len(result) == 0

    def test_handles_malformed_tool_result_json(self):
        """Should handle malformed JSON in tool_result gracefully."""
        converter = LangChainHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": "not valid json",
                "message_type": "tool_result",
            }
        ]

        result = converter.convert(raw)

        assert len(result) == 0

    def test_skips_tool_result_without_matching_call(self):
        """Should skip orphaned tool results that would violate LangChain pairing."""
        converter = LangChainHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_end",
                        "name": "orphan_tool",
                        "run_id": "run-orphan",
                        "data": {"output": "result tool_call_id='call_orphan'"},
                    }
                ),
                "message_type": "tool_result",
            }
        ]

        result = converter.convert(raw)

        assert result == []


class TestExtractToolCallId:
    """Tests for _extract_tool_call_id helper."""

    def test_extracts_tool_call_id(self):
        """Should extract tool_call_id from output string."""
        output = "content='OK' name='thenvoi_send_message' tool_call_id='call_wzMushVvBFE8tBwupFDEZQGE'"

        result = LangChainHistoryConverter._extract_tool_call_id(output)

        assert result == "call_wzMushVvBFE8tBwupFDEZQGE"

    def test_returns_none_when_not_found(self):
        """Should return None when tool_call_id not in string."""
        output = "Simple output without tool_call_id"

        result = LangChainHistoryConverter._extract_tool_call_id(output)

        assert result is None

    def test_handles_complex_output_string(self):
        """Should handle complex output strings."""
        output = """ToolContent(content='{"status": "success", "data": {"id": 123}}' name='api_call' tool_call_id='call_123abc')"""

        result = LangChainHistoryConverter._extract_tool_call_id(output)

        assert result == "call_123abc"

    def test_extracts_tool_call_id_from_structured_output(self):
        output = {"content": "OK", "tool_call_id": "call_structured"}

        result = LangChainHistoryConverter._extract_tool_call_id(output)

        assert result == "call_structured"

    def test_extracts_tool_call_id_from_nested_structured_output(self):
        output = {"messages": [{"content": "OK", "tool_call_id": "call_nested"}]}

        result = LangChainHistoryConverter._extract_tool_call_id(output)

        assert result == "call_nested"

    def test_ignores_unrelated_id_fields(self):
        output = {"id": "message-id", "content": "OK"}

        result = LangChainHistoryConverter._extract_tool_call_id(output)

        assert result is None

    def test_extracts_tool_call_id_from_message_like_object(self):
        class ToolOutput:
            tool_call_id = "call_object"

        result = LangChainHistoryConverter._extract_tool_call_id(ToolOutput())

        assert result == "call_object"


class TestMixedHistory:
    """Integration tests with mixed message types."""

    def test_includes_own_agent_text_when_no_tool_events_exist(self):
        converter = LangChainHistoryConverter(agent_name="Agent")
        raw = [
            {
                "role": "user",
                "content": "Remember the word papaya.",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": "I will remember papaya.",
                "sender_name": "Agent",
                "message_type": "text",
            },
            {
                "role": "user",
                "content": "What word did I ask you to remember?",
                "sender_name": "Alice",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        assert len(result) == 3
        assert isinstance(result[0], HumanMessage)
        assert result[0].content == "[Alice]: Remember the word papaya."
        assert isinstance(result[1], AIMessage)
        assert result[1].content == "I will remember papaya."
        assert isinstance(result[2], HumanMessage)
        assert result[2].content == "[Alice]: What word did I ask you to remember?"

    def test_skips_own_agent_text_while_tool_call_is_open(self):
        converter = LangChainHistoryConverter(agent_name="Agent")
        raw = [
            {
                "role": "user",
                "content": "Search for Python tutorials",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_start",
                        "name": "search",
                        "run_id": "run-1",
                        "data": {"input": {"query": "Python tutorials"}},
                    }
                ),
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": "I am searching now.",
                "sender_name": "Agent",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_end",
                        "name": "search",
                        "run_id": "run-1",
                        "data": {"output": "Found results tool_call_id='call_search'"},
                    }
                ),
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert len(result) == 3
        assert isinstance(result[0], HumanMessage)
        assert isinstance(result[1], AIMessage)
        assert result[1].tool_calls[0]["name"] == "search"
        assert isinstance(result[2], ToolMessage)

    def test_preserves_own_visible_text_outside_active_tool_segments(self):
        converter = LangChainHistoryConverter(agent_name="Agent")
        raw = [
            {
                "role": "user",
                "content": "Remember the word papaya.",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": "I will remember papaya.",
                "sender_name": "Agent",
                "message_type": "text",
            },
            {
                "role": "user",
                "content": "Search for Python tutorials",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_start",
                        "name": "search",
                        "run_id": "run-1",
                        "data": {"input": {"query": "Python tutorials"}},
                    }
                ),
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": "I am searching now.",
                "sender_name": "Agent",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_end",
                        "name": "search",
                        "run_id": "run-1",
                        "data": {"output": "Found results tool_call_id='call_search'"},
                    }
                ),
                "message_type": "tool_result",
            },
            {
                "role": "assistant",
                "content": "I found Python tutorials for you.",
                "sender_name": "Agent",
                "message_type": "text",
            },
            {
                "role": "user",
                "content": "What word did I ask you to remember?",
                "sender_name": "Alice",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        assert len(result) == 7
        assert isinstance(result[0], HumanMessage)
        assert result[0].content == "[Alice]: Remember the word papaya."
        assert isinstance(result[1], AIMessage)
        assert result[1].content == "I will remember papaya."
        assert isinstance(result[2], HumanMessage)
        assert result[2].content == "[Alice]: Search for Python tutorials"
        assert isinstance(result[3], AIMessage)
        assert result[3].tool_calls[0]["name"] == "search"
        assert isinstance(result[4], ToolMessage)
        assert isinstance(result[5], AIMessage)
        assert result[5].content == "I found Python tutorials for you."
        assert isinstance(result[6], HumanMessage)
        assert result[6].content == "[Alice]: What word did I ask you to remember?"

    def test_full_conversation_flow(self):
        """Should handle a realistic conversation with all message types."""
        converter = LangChainHistoryConverter(agent_name="Agent")
        raw = [
            # User asks question
            {
                "role": "user",
                "content": "Search for Python tutorials",
                "sender_name": "Alice",
                "message_type": "text",
            },
            # Agent uses tool
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_start",
                        "name": "search",
                        "run_id": "run-1",
                        "data": {"input": {"query": "Python tutorials"}},
                    }
                ),
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "event": "on_tool_end",
                        "name": "search",
                        "run_id": "run-1",
                        "data": {
                            "output": "Found 5 results tool_call_id='call_search'"
                        },
                    }
                ),
                "message_type": "tool_result",
            },
            # Agent's visible answer after the tool result should be preserved.
            {
                "role": "assistant",
                "content": "I found 5 Python tutorials for you.",
                "sender_name": "Agent",
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

        # Should have: HumanMessage, AIMessage with tool_calls, ToolMessage,
        # visible AIMessage answer, HumanMessage.
        assert len(result) == 5

        assert isinstance(result[0], HumanMessage)
        assert result[0].content == "[Alice]: Search for Python tutorials"

        assert isinstance(result[1], AIMessage)
        assert result[1].tool_calls[0]["name"] == "search"

        assert isinstance(result[2], ToolMessage)

        assert isinstance(result[3], AIMessage)
        assert result[3].content == "I found 5 Python tutorials for you."

        assert isinstance(result[4], HumanMessage)
        assert result[4].content == "[Alice]: Thanks!"

    def test_multi_agent_conversation_flow(self):
        """Should include other agents' messages in multi-agent conversations."""
        converter = LangChainHistoryConverter(agent_name="Main Agent")
        raw = [
            # User asks Main Agent to get weather
            {
                "role": "user",
                "content": "What's the weather in Tokyo?",
                "sender_name": "Alice",
                "message_type": "text",
            },
            # Main Agent asks Weather Agent
            {
                "role": "assistant",
                "content": "Let me check with the weather agent.",
                "sender_name": "Main Agent",
                "message_type": "text",
            },
            # Weather Agent responds
            {
                "role": "assistant",
                "content": "Tokyo is 15°C and cloudy.",
                "sender_name": "Weather Agent",
                "message_type": "text",
            },
            # Main Agent relays the response
            {
                "role": "assistant",
                "content": "The weather in Tokyo is 15°C and cloudy.",
                "sender_name": "Main Agent",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        # Should have Alice's message, Main Agent's own replies, and Weather Agent's message.
        # With no tool events, own replies are needed to rehydrate a two-sided transcript.
        assert len(result) == 4

        assert isinstance(result[0], HumanMessage)
        assert result[0].content == "[Alice]: What's the weather in Tokyo?"

        assert isinstance(result[1], AIMessage)
        assert result[1].content == "Let me check with the weather agent."

        assert isinstance(result[2], HumanMessage)
        assert result[2].content == "[Weather Agent]: Tokyo is 15°C and cloudy."

        assert isinstance(result[3], AIMessage)
        assert result[3].content == "The weather in Tokyo is 15°C and cloudy."
