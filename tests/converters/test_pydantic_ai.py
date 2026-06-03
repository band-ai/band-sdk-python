"""Tests for PydanticAIHistoryConverter.

Tests for shared converter behavior (user messages, agent filtering, empty
history, edge cases) live in tests/framework_conformance/test_converter_conformance.py.
This file contains PydanticAI-specific tool event conversion (batching,
ModelRequest/ModelResponse types, RetryPromptPart for errors) and mixed
history integration tests.
"""

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from band.converters.pydantic_ai import PydanticAIHistoryConverter


class TestToolEventConversion:
    """Tests for tool_call and tool_result conversion."""

    def test_converts_tool_call_to_model_response(self):
        """tool_call messages become ModelResponse with ToolCallPart."""
        converter = PydanticAIHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {"query": "test"}, "tool_call_id": "call_123"}',
                "message_type": "tool_call",
            }
        ]

        result = converter.convert(raw)

        assert len(result) == 1
        assert isinstance(result[0], ModelResponse)
        assert len(result[0].parts) == 1
        assert isinstance(result[0].parts[0], ToolCallPart)
        assert result[0].parts[0].tool_name == "search"
        assert result[0].parts[0].args == {"query": "test"}
        assert result[0].parts[0].tool_call_id == "call_123"

    def test_converts_tool_result_to_model_request(self):
        """tool_result messages become ModelRequest with ToolReturnPart."""
        converter = PydanticAIHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {"query": "test"}, "tool_call_id": "call_123"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "search", "output": "result data", "tool_call_id": "call_123"}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert len(result) == 2
        # First message is the ToolCallPart
        assert isinstance(result[0], ModelResponse)
        assert isinstance(result[0].parts[0], ToolCallPart)
        # Second message is the ToolReturnPart
        assert isinstance(result[1], ModelRequest)
        assert len(result[1].parts) == 1
        assert isinstance(result[1].parts[0], ToolReturnPart)
        assert result[1].parts[0].tool_name == "search"
        assert result[1].parts[0].content == "result data"
        assert result[1].parts[0].tool_call_id == "call_123"

    def test_batches_multiple_tool_calls(self):
        """Multiple consecutive tool_call messages are batched into one ModelResponse."""
        converter = PydanticAIHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "tool1", "args": {}, "tool_call_id": "call_1"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "args": {}, "tool_call_id": "call_2"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool1", "output": "result1", "tool_call_id": "call_1"}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        # First message should have both ToolCallParts batched
        assert len(result) == 2
        assert isinstance(result[0], ModelResponse)
        assert len(result[0].parts) == 2
        assert result[0].parts[0].tool_name == "tool1"
        assert result[0].parts[1].tool_name == "tool2"
        # Second message is the ToolReturnPart
        assert isinstance(result[1], ModelRequest)
        assert isinstance(result[1].parts[0], ToolReturnPart)

    def test_batches_multiple_tool_results(self):
        """Multiple consecutive tool_result messages are batched into one ModelRequest.

        Similar to Anthropic's requirement, tool results should be batched
        together to enable parallel tool use patterns.
        """
        converter = PydanticAIHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "tool1", "args": {}, "tool_call_id": "call_1"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "args": {}, "tool_call_id": "call_2"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool1", "output": "result1", "tool_call_id": "call_1"}',
                "message_type": "tool_result",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "output": "result2", "tool_call_id": "call_2"}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        # Should have: batched ModelResponse, batched ModelRequest with ToolReturnParts
        assert len(result) == 2

        # First message: batched ModelResponse
        assert isinstance(result[0], ModelResponse)
        assert len(result[0].parts) == 2
        assert result[0].parts[0].tool_name == "tool1"
        assert result[0].parts[1].tool_name == "tool2"

        # Second message: batched ModelRequest with ToolReturnParts
        assert isinstance(result[1], ModelRequest)
        assert len(result[1].parts) == 2
        assert isinstance(result[1].parts[0], ToolReturnPart)
        assert result[1].parts[0].tool_call_id == "call_1"
        assert isinstance(result[1].parts[1], ToolReturnPart)
        assert result[1].parts[1].tool_call_id == "call_2"

    def test_handles_interleaved_tool_calls_and_results(self):
        """Interleaved tool_call and tool_result messages are handled correctly."""
        converter = PydanticAIHistoryConverter()
        raw = [
            # First batch of tool calls
            {
                "role": "assistant",
                "content": '{"name": "tool1", "args": {"q": "a"}, "tool_call_id": "call_1"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "args": {"q": "b"}, "tool_call_id": "call_2"}',
                "message_type": "tool_call",
            },
            # Results for first batch
            {
                "role": "assistant",
                "content": '{"name": "tool1", "output": "result1", "tool_call_id": "call_1"}',
                "message_type": "tool_result",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "output": "result2", "tool_call_id": "call_2"}',
                "message_type": "tool_result",
            },
            # Second batch of tool calls
            {
                "role": "assistant",
                "content": '{"name": "tool3", "args": {}, "tool_call_id": "call_3"}',
                "message_type": "tool_call",
            },
            # Result for second batch
            {
                "role": "assistant",
                "content": '{"name": "tool3", "output": "result3", "tool_call_id": "call_3"}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        # Expected: [batched ModelResponse (2), batched ModelRequest (2), ModelResponse, ModelRequest]
        assert len(result) == 4

        # First message: batched ModelResponse with ToolCallParts for tool1 and tool2
        assert isinstance(result[0], ModelResponse)
        assert len(result[0].parts) == 2
        assert result[0].parts[0].tool_name == "tool1"
        assert result[0].parts[1].tool_name == "tool2"

        # Second message: batched ModelRequest with ToolReturnParts
        assert isinstance(result[1], ModelRequest)
        assert len(result[1].parts) == 2
        assert isinstance(result[1].parts[0], ToolReturnPart)
        assert result[1].parts[0].tool_call_id == "call_1"
        assert isinstance(result[1].parts[1], ToolReturnPart)
        assert result[1].parts[1].tool_call_id == "call_2"

        # Third message: single ModelResponse for tool3
        assert isinstance(result[2], ModelResponse)
        assert len(result[2].parts) == 1
        assert result[2].parts[0].tool_name == "tool3"

        # Fourth message: ModelRequest with ToolReturnPart for tool3
        assert isinstance(result[3], ModelRequest)
        assert len(result[3].parts) == 1
        assert isinstance(result[3].parts[0], ToolReturnPart)
        assert result[3].parts[0].tool_call_id == "call_3"

    def test_handles_malformed_tool_call_json(self, caplog):
        """Malformed tool_call JSON is skipped with warning."""
        converter = PydanticAIHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": "not valid json",
                "message_type": "tool_call",
            }
        ]

        result = converter.convert(raw)

        assert len(result) == 0
        assert "Failed to parse tool_call" in caplog.text

    def test_handles_malformed_tool_result_json(self, caplog):
        """Malformed tool_result JSON is skipped with warning."""
        converter = PydanticAIHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": "not valid json",
                "message_type": "tool_result",
            }
        ]

        result = converter.convert(raw)

        assert len(result) == 0
        assert "Failed to parse tool_result" in caplog.text

    def test_skips_tool_call_with_missing_tool_call_id(self, caplog):
        """tool_call without tool_call_id is skipped."""
        converter = PydanticAIHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {"query": "test"}}',
                "message_type": "tool_call",
            }
        ]

        result = converter.convert(raw)

        assert len(result) == 0
        assert "Skipping tool_call with missing tool_call_id" in caplog.text

    def test_skips_tool_call_with_missing_name(self, caplog):
        """tool_call without name is skipped."""
        converter = PydanticAIHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"args": {"query": "test"}, "tool_call_id": "call_123"}',
                "message_type": "tool_call",
            }
        ]

        result = converter.convert(raw)

        assert len(result) == 0
        assert "Skipping tool_call with missing name" in caplog.text

    def test_skips_tool_result_with_missing_tool_call_id(self, caplog):
        """tool_result without tool_call_id is skipped."""
        converter = PydanticAIHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "output": "result data"}',
                "message_type": "tool_result",
            }
        ]

        result = converter.convert(raw)

        assert len(result) == 0
        assert "Skipping tool_result with missing tool_call_id" in caplog.text

    def test_skips_tool_result_with_missing_name(self, caplog):
        """tool_result without name is skipped."""
        converter = PydanticAIHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"output": "result data", "tool_call_id": "call_123"}',
                "message_type": "tool_result",
            }
        ]

        result = converter.convert(raw)

        assert len(result) == 0
        assert "Skipping tool_result with missing name" in caplog.text

    def test_flushes_trailing_tool_calls(self):
        """Trailing tool_call messages at end of history are properly flushed."""
        converter = PydanticAIHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "tool1", "args": {}, "tool_call_id": "call_1"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "args": {}, "tool_call_id": "call_2"}',
                "message_type": "tool_call",
            },
        ]

        result = converter.convert(raw)

        # Both tool calls should be batched into a single ModelResponse
        assert len(result) == 1
        assert isinstance(result[0], ModelResponse)
        assert len(result[0].parts) == 2
        assert result[0].parts[0].tool_name == "tool1"
        assert result[0].parts[1].tool_name == "tool2"

    def test_uses_retry_prompt_part_for_error_results(self):
        """tool_result with is_error=True uses RetryPromptPart instead of ToolReturnPart."""
        converter = PydanticAIHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {}, "tool_call_id": "call_123"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "search", "output": "Error: API failed", "tool_call_id": "call_123", "is_error": true}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert len(result) == 2
        # First message is the ToolCallPart
        assert isinstance(result[0], ModelResponse)
        assert isinstance(result[0].parts[0], ToolCallPart)
        # Second message should be RetryPromptPart (not ToolReturnPart)
        assert isinstance(result[1], ModelRequest)
        assert len(result[1].parts) == 1
        assert isinstance(result[1].parts[0], RetryPromptPart)
        assert result[1].parts[0].content == "Error: API failed"
        assert result[1].parts[0].tool_name == "search"
        assert result[1].parts[0].tool_call_id == "call_123"

    def test_uses_tool_return_part_for_success_results(self):
        """tool_result with is_error=False uses ToolReturnPart."""
        converter = PydanticAIHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {}, "tool_call_id": "call_123"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "search", "output": "result data", "tool_call_id": "call_123", "is_error": false}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert len(result) == 2
        assert isinstance(result[1], ModelRequest)
        assert isinstance(result[1].parts[0], ToolReturnPart)
        assert result[1].parts[0].content == "result data"

    def test_batches_mixed_success_and_error_results(self):
        """Mixed success and error results are batched correctly."""
        converter = PydanticAIHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "tool1", "args": {}, "tool_call_id": "call_1"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "args": {}, "tool_call_id": "call_2"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool1", "output": "success", "tool_call_id": "call_1", "is_error": false}',
                "message_type": "tool_result",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "output": "Error: failed", "tool_call_id": "call_2", "is_error": true}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        # Should have: batched tool calls, batched tool results
        assert len(result) == 2

        # Second message should have mixed ToolReturnPart and RetryPromptPart
        assert isinstance(result[1], ModelRequest)
        assert len(result[1].parts) == 2
        # First result is success (ToolReturnPart)
        assert isinstance(result[1].parts[0], ToolReturnPart)
        assert result[1].parts[0].content == "success"
        # Second result is error (RetryPromptPart)
        assert isinstance(result[1].parts[1], RetryPromptPart)
        assert result[1].parts[1].content == "Error: failed"


class TestMixedHistory:
    """Integration tests with mixed message types."""

    def test_full_conversation_flow(self):
        """Should handle a realistic conversation with mixed message types."""
        converter = PydanticAIHistoryConverter(agent_name="Agent")
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
                "content": '{"name": "get_weather", "args": {"location": "NYC"}, "tool_call_id": "call_123"}',
                "message_type": "tool_call",
            },
            # Tool result
            {
                "role": "assistant",
                "content": '{"name": "get_weather", "output": "sunny", "tool_call_id": "call_123"}',
                "message_type": "tool_result",
            },
            # Agent responds with text (skipped - own message)
            {
                "role": "assistant",
                "content": "It's sunny today!",
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

        # Should have: user message, tool_call, tool_result, user follow-up
        # (agent's own text is skipped)
        assert len(result) == 4

        # User question
        assert isinstance(result[0], ModelRequest)
        assert isinstance(result[0].parts[0], UserPromptPart)
        assert result[0].parts[0].content == "[Alice]: What's the weather?"

        # Tool call (ModelResponse)
        assert isinstance(result[1], ModelResponse)
        assert isinstance(result[1].parts[0], ToolCallPart)
        assert result[1].parts[0].tool_name == "get_weather"

        # Tool result (ModelRequest with ToolReturnPart)
        assert isinstance(result[2], ModelRequest)
        assert isinstance(result[2].parts[0], ToolReturnPart)
        assert result[2].parts[0].content == "sunny"

        # User follow-up
        assert isinstance(result[3], ModelRequest)
        assert isinstance(result[3].parts[0], UserPromptPart)
        assert result[3].parts[0].content == "[Alice]: Thanks!"

    def test_multi_user_conversation(self):
        """Handles multiple users in conversation."""
        converter = PydanticAIHistoryConverter(agent_name="Agent")
        raw = [
            {
                "role": "user",
                "content": "Hi team!",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "role": "user",
                "content": "Hello everyone!",
                "sender_name": "Bob",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": "Hello Alice and Bob!",
                "sender_name": "Agent",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        # Agent's own message is skipped
        assert len(result) == 2
        assert result[0].parts[0].content == "[Alice]: Hi team!"
        assert result[1].parts[0].content == "[Bob]: Hello everyone!"

    def test_multi_agent_conversation_flow(self):
        """Should include other agents' messages in multi-agent conversations."""
        converter = PydanticAIHistoryConverter(agent_name="Main Agent")
        raw = [
            # User asks Main Agent to get weather
            {
                "role": "user",
                "content": "What's the weather in Tokyo?",
                "sender_name": "Alice",
                "message_type": "text",
            },
            # Main Agent asks Weather Agent (skipped - own message)
            {
                "role": "assistant",
                "content": "Let me check with the weather agent.",
                "sender_name": "Main Agent",
                "message_type": "text",
            },
            # Weather Agent responds (included)
            {
                "role": "assistant",
                "content": "Tokyo is 15°C and cloudy.",
                "sender_name": "Weather Agent",
                "message_type": "text",
            },
            # Main Agent relays the response (skipped - own message)
            {
                "role": "assistant",
                "content": "The weather in Tokyo is 15°C and cloudy.",
                "sender_name": "Main Agent",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        # Should have: Alice's message + Weather Agent's message
        # (Main Agent's own messages are skipped)
        assert len(result) == 2

        assert isinstance(result[0], ModelRequest)
        assert result[0].parts[0].content == "[Alice]: What's the weather in Tokyo?"

        assert isinstance(result[1], ModelRequest)
        assert (
            result[1].parts[0].content == "[Weather Agent]: Tokyo is 15°C and cloudy."
        )
