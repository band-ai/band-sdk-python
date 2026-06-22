"""Tests for AnthropicHistoryConverter.

Tests for shared converter behavior (user messages, agent filtering, empty
history, edge cases, output shape) live in
tests/framework_conformance/test_converter_conformance.py.
This file contains Anthropic-specific tool event conversion and integration tests.
"""

from band.converters.anthropic import AnthropicHistoryConverter


class TestTextMessageConversion:
    def test_missing_agent_name_does_not_classify_unknown_assistant_as_own_response(
        self,
    ):
        """Unknown assistant rows stay attributed unless the agent name is configured."""
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": "Unattributed assistant text",
                "sender_name": "",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        assert result == [{"role": "user", "content": "Unattributed assistant text"}]

    def test_legacy_message_type_is_converted_as_text(self):
        converter = AnthropicHistoryConverter(agent_name="Agent")
        raw = [
            {
                "role": "assistant",
                "content": "Legacy own text",
                "sender_name": "Agent",
                "message_type": "message",
            }
        ]

        result = converter.convert(raw)

        assert result == [{"role": "assistant", "content": "Legacy own text"}]


class TestToolEventConversion:
    """Tests for tool_call and tool_result conversion."""

    def test_converts_tool_call_to_assistant_message(self):
        """tool_call messages become assistant messages with tool_use content.

        An orphaned tool_use (no matching tool_result) also gets a synthetic
        error tool_result appended so the history satisfies the Anthropic API.
        """
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {"query": "test"}, "tool_call_id": "toolu_123"}',
                "message_type": "tool_call",
            }
        ]

        result = converter.convert(raw)

        # assistant tool_use + synthetic user tool_result (orphan patching)
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert isinstance(result[0]["content"], list)
        assert result[0]["content"][0]["type"] == "tool_use"
        assert result[0]["content"][0]["id"] == "toolu_123"
        assert result[0]["content"][0]["name"] == "search"
        assert result[0]["content"][0]["input"] == {"query": "test"}
        # Synthetic tool_result for the orphan
        assert result[1]["role"] == "user"
        assert result[1]["content"][0]["type"] == "tool_result"
        assert result[1]["content"][0]["tool_use_id"] == "toolu_123"
        assert result[1]["content"][0]["is_error"] is True

    def test_converts_tool_result_to_user_message(self):
        """tool_result messages become user messages with tool_result content."""
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {"query": "test"}, "tool_call_id": "toolu_123"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "search", "output": "result data", "tool_call_id": "toolu_123"}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert len(result) == 2
        # First message is the tool_use
        assert result[0]["role"] == "assistant"
        assert result[0]["content"][0]["type"] == "tool_use"
        # Second message is the tool_result
        assert result[1]["role"] == "user"
        assert isinstance(result[1]["content"], list)
        assert result[1]["content"][0]["type"] == "tool_result"
        assert result[1]["content"][0]["tool_use_id"] == "toolu_123"
        assert result[1]["content"][0]["content"] == "result data"

    def test_batches_multiple_tool_calls(self):
        """Multiple consecutive tool_call messages are batched into one assistant message."""
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "tool1", "args": {}, "tool_call_id": "toolu_1"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "args": {}, "tool_call_id": "toolu_2"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool1", "output": "result1", "tool_call_id": "toolu_1"}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        # First message should have both tool_use blocks batched
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert len(result[0]["content"]) == 2
        assert result[0]["content"][0]["name"] == "tool1"
        assert result[0]["content"][1]["name"] == "tool2"
        # Second message is the tool_result
        assert result[1]["role"] == "user"

    def test_batches_multiple_tool_results(self):
        """Multiple consecutive tool_result messages are batched into one user message.

        Per Anthropic docs, all tool results must be in a single user message
        to enable parallel tool use.
        """
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "tool1", "args": {}, "tool_call_id": "toolu_1"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "args": {}, "tool_call_id": "toolu_2"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool1", "output": "result1", "tool_call_id": "toolu_1"}',
                "message_type": "tool_result",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "output": "result2", "tool_call_id": "toolu_2"}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        # Should have: batched tool_use, batched tool_results
        assert len(result) == 2

        # First message: batched tool_use
        assert result[0]["role"] == "assistant"
        assert len(result[0]["content"]) == 2
        assert result[0]["content"][0]["name"] == "tool1"
        assert result[0]["content"][1]["name"] == "tool2"

        # Second message: batched tool_results
        assert result[1]["role"] == "user"
        assert len(result[1]["content"]) == 2
        assert result[1]["content"][0]["tool_use_id"] == "toolu_1"
        assert result[1]["content"][1]["tool_use_id"] == "toolu_2"

    def test_handles_interleaved_tool_calls_and_results(self):
        """Interleaved tool_call and tool_result messages are handled correctly."""
        converter = AnthropicHistoryConverter()
        raw = [
            # First batch of tool calls
            {
                "role": "assistant",
                "content": '{"name": "tool1", "args": {"q": "a"}, "tool_call_id": "toolu_1"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "args": {"q": "b"}, "tool_call_id": "toolu_2"}',
                "message_type": "tool_call",
            },
            # Results for first batch
            {
                "role": "assistant",
                "content": '{"name": "tool1", "output": "result1", "tool_call_id": "toolu_1"}',
                "message_type": "tool_result",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "output": "result2", "tool_call_id": "toolu_2"}',
                "message_type": "tool_result",
            },
            # Second batch of tool calls
            {
                "role": "assistant",
                "content": '{"name": "tool3", "args": {}, "tool_call_id": "toolu_3"}',
                "message_type": "tool_call",
            },
            # Result for second batch
            {
                "role": "assistant",
                "content": '{"name": "tool3", "output": "result3", "tool_call_id": "toolu_3"}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        # Expected: [batched tool_use (2), batched tool_results (2), tool_use, tool_result]
        assert len(result) == 4

        # First message: batched tool_use for tool1 and tool2
        assert result[0]["role"] == "assistant"
        assert len(result[0]["content"]) == 2
        assert result[0]["content"][0]["name"] == "tool1"
        assert result[0]["content"][1]["name"] == "tool2"

        # Second message: batched tool_results for tool1 and tool2
        assert result[1]["role"] == "user"
        assert len(result[1]["content"]) == 2
        assert result[1]["content"][0]["tool_use_id"] == "toolu_1"
        assert result[1]["content"][1]["tool_use_id"] == "toolu_2"

        # Third message: single tool_use for tool3
        assert result[2]["role"] == "assistant"
        assert len(result[2]["content"]) == 1
        assert result[2]["content"][0]["name"] == "tool3"

        # Fourth message: tool_result for tool3
        assert result[3]["role"] == "user"
        assert len(result[3]["content"]) == 1
        assert result[3]["content"][0]["tool_use_id"] == "toolu_3"

    def test_handles_malformed_tool_call_json(self, caplog):
        """Malformed tool_call JSON is skipped with warning."""
        converter = AnthropicHistoryConverter()
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
        converter = AnthropicHistoryConverter()
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
        converter = AnthropicHistoryConverter()
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
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"args": {"query": "test"}, "tool_call_id": "toolu_123"}',
                "message_type": "tool_call",
            }
        ]

        result = converter.convert(raw)

        assert len(result) == 0
        assert "Skipping tool_call with missing name" in caplog.text

    def test_skips_tool_result_with_missing_tool_call_id(self, caplog):
        """tool_result without tool_call_id is skipped."""
        converter = AnthropicHistoryConverter()
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
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"output": "result data", "tool_call_id": "toolu_123"}',
                "message_type": "tool_result",
            }
        ]

        result = converter.convert(raw)

        assert len(result) == 0
        assert "Skipping tool_result with missing name" in caplog.text

    def test_flushes_trailing_tool_calls(self):
        """Trailing tool_call messages at end of history are properly flushed.

        Orphaned tool_use blocks also get synthetic tool_results.
        """
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "tool1", "args": {}, "tool_call_id": "toolu_1"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "args": {}, "tool_call_id": "toolu_2"}',
                "message_type": "tool_call",
            },
        ]

        result = converter.convert(raw)

        # Both tool calls batched into one assistant message + synthetic results
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert len(result[0]["content"]) == 2
        assert result[0]["content"][0]["name"] == "tool1"
        assert result[0]["content"][1]["name"] == "tool2"
        # Synthetic tool_results for both orphans
        assert result[1]["role"] == "user"
        assert len(result[1]["content"]) == 2
        result_ids = {b["tool_use_id"] for b in result[1]["content"]}
        assert result_ids == {"toolu_1", "toolu_2"}

    def test_preserves_is_error_field_when_true(self):
        """tool_result with is_error=True includes the field in output."""
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {}, "tool_call_id": "toolu_123"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "search", "output": "Error: API failed", "tool_call_id": "toolu_123", "is_error": true}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert len(result) == 2
        tool_result_block = result[1]["content"][0]
        assert tool_result_block["type"] == "tool_result"
        assert tool_result_block["is_error"] is True
        assert tool_result_block["content"] == "Error: API failed"

    def test_omits_is_error_field_when_false(self):
        """tool_result with is_error=False omits the field (Anthropic default)."""
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {}, "tool_call_id": "toolu_123"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "search", "output": "result", "tool_call_id": "toolu_123", "is_error": false}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert len(result) == 2
        tool_result_block = result[1]["content"][0]
        assert tool_result_block["type"] == "tool_result"
        assert "is_error" not in tool_result_block  # Field should be omitted when False


class TestVisibleTextHistory:
    def test_preserves_own_agent_visible_text_as_assistant_history(self):
        converter = AnthropicHistoryConverter(agent_name="Test Agent")
        raw = [
            {
                "role": "assistant",
                "content": "I previously said LIGHTHOUSE.",
                "sender_name": "Test Agent",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        assert result == [
            {
                "role": "assistant",
                "content": "I previously said LIGHTHOUSE.",
            }
        ]


class TestOrphanedToolUseSanitization:
    """Tests for orphaned tool_use block handling."""

    def test_patches_trailing_orphaned_tool_use(self):
        """tool_use at end of history without tool_result gets synthetic result."""
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {"q": "test"}, "tool_call_id": "toolu_1"}',
                "message_type": "tool_call",
            },
        ]

        result = converter.convert(raw)

        # Should have: assistant tool_use + synthetic user tool_result
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert result[0]["content"][0]["type"] == "tool_use"
        assert result[1]["role"] == "user"
        assert result[1]["content"][0]["type"] == "tool_result"
        assert result[1]["content"][0]["tool_use_id"] == "toolu_1"
        assert result[1]["content"][0]["is_error"] is True
        assert "interrupted" in result[1]["content"][0]["content"]

    def test_patches_multiple_orphaned_tool_uses(self):
        """Multiple orphaned tool_use blocks all get synthetic results."""
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "tool1", "args": {}, "tool_call_id": "toolu_1"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "args": {}, "tool_call_id": "toolu_2"}',
                "message_type": "tool_call",
            },
        ]

        result = converter.convert(raw)

        # Should have: batched assistant tool_use + synthetic user tool_results
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert len(result[0]["content"]) == 2
        assert result[1]["role"] == "user"
        assert len(result[1]["content"]) == 2
        result_ids = {b["tool_use_id"] for b in result[1]["content"]}
        assert result_ids == {"toolu_1", "toolu_2"}

    def test_patches_partial_orphan_in_batch(self):
        """When some tool_uses have results and some don't, patches only orphans."""
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "tool1", "args": {}, "tool_call_id": "toolu_1"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "args": {}, "tool_call_id": "toolu_2"}',
                "message_type": "tool_call",
            },
            # Only tool1 has a result; tool2 is orphaned
            {
                "role": "assistant",
                "content": '{"name": "tool1", "output": "ok", "tool_call_id": "toolu_1"}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        # Should have: batched assistant tool_use + user tool_results (real + synthetic)
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert len(result[0]["content"]) == 2

        assert result[1]["role"] == "user"
        result_blocks = result[1]["content"]
        result_map = {b["tool_use_id"]: b for b in result_blocks}
        # tool1 has real result
        assert result_map["toolu_1"]["content"] == "ok"
        assert "is_error" not in result_map["toolu_1"]
        # tool2 has synthetic error result
        assert result_map["toolu_2"]["is_error"] is True
        assert "interrupted" in result_map["toolu_2"]["content"]

    def test_no_patch_when_all_results_present(self):
        """Complete tool_use/tool_result pairs are left untouched."""
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {}, "tool_call_id": "toolu_1"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "search", "output": "found it", "tool_call_id": "toolu_1"}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert len(result) == 2
        assert result[1]["content"][0]["content"] == "found it"
        assert "is_error" not in result[1]["content"][0]

    def test_patches_orphan_followed_by_text(self):
        """tool_use followed by text (no tool_result) merges into user msg."""
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {}, "tool_call_id": "toolu_1"}',
                "message_type": "tool_call",
            },
            # Text message instead of tool_result
            {
                "role": "user",
                "content": "What happened?",
                "sender_name": "Alice",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        # Should merge synthetic tool_result into the user message
        # to avoid consecutive user messages (violates alternating turns)
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert result[0]["content"][0]["type"] == "tool_use"
        # User message has synthetic tool_result prepended + converted text
        assert result[1]["role"] == "user"
        assert isinstance(result[1]["content"], list)
        assert result[1]["content"][0]["type"] == "tool_result"
        assert result[1]["content"][0]["is_error"] is True
        assert result[1]["content"][1]["type"] == "text"
        assert "[Alice]" in result[1]["content"][1]["text"]

    def test_logs_warning_for_orphaned_tool_uses(self, caplog):
        """Should log warning when patching orphaned tool_use blocks."""
        converter = AnthropicHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {}, "tool_call_id": "toolu_1"}',
                "message_type": "tool_call",
            },
        ]

        converter.convert(raw)

        assert "orphaned tool_use" in caplog.text.lower()


class TestMixedHistory:
    """Integration tests with mixed message types."""

    def test_full_conversation_flow(self):
        """Should handle a realistic conversation with mixed message types."""
        converter = AnthropicHistoryConverter(agent_name="Agent")
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
                "content": '{"name": "get_weather", "args": {"location": "NYC"}, "tool_call_id": "toolu_123"}',
                "message_type": "tool_call",
            },
            # Tool result
            {
                "role": "assistant",
                "content": '{"name": "get_weather", "output": "sunny", "tool_call_id": "toolu_123"}',
                "message_type": "tool_result",
            },
            # Agent responds with visible text
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

        # Should have: user message, tool_use, tool_result, agent text, user follow-up
        assert len(result) == 5

        # User question
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "[Alice]: What's the weather?"

        # Tool use (assistant)
        assert result[1]["role"] == "assistant"
        assert result[1]["content"][0]["type"] == "tool_use"
        assert result[1]["content"][0]["name"] == "get_weather"

        # Tool result (user)
        assert result[2]["role"] == "user"
        assert result[2]["content"][0]["type"] == "tool_result"
        assert result[2]["content"][0]["content"] == "sunny"

        # Agent visible text
        assert result[3]["role"] == "assistant"
        assert result[3]["content"] == "It's sunny today!"

        # User follow-up
        assert result[4]["role"] == "user"
        assert result[4]["content"] == "[Alice]: Thanks!"

    def test_multi_user_conversation(self):
        """Handles multiple users in conversation."""
        converter = AnthropicHistoryConverter(agent_name="Agent")
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

        assert len(result) == 3
        assert result[0]["content"] == "[Alice]: Hi team!"
        assert result[1]["content"] == "[Bob]: Hello everyone!"
        assert result[2]["role"] == "assistant"
        assert result[2]["content"] == "Hello Alice and Bob!"

    def test_multi_agent_conversation_flow(self):
        """Should include other agents' messages in multi-agent conversations."""
        converter = AnthropicHistoryConverter(agent_name="Main Agent")
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
            # Weather Agent responds (included)
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

        assert len(result) == 4

        assert result[0]["role"] == "user"
        assert result[0]["content"] == "[Alice]: What's the weather in Tokyo?"

        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "Let me check with the weather agent."

        assert result[2]["role"] == "user"
        assert result[2]["content"] == "[Weather Agent]: Tokyo is 15°C and cloudy."

        assert result[3]["role"] == "assistant"
        assert result[3]["content"] == "The weather in Tokyo is 15°C and cloudy."
