"""Tests for GeminiHistoryConverter."""

from __future__ import annotations

from band.converters.gemini import GeminiHistoryConverter


class TestToolEventConversion:
    """Tests for tool_call and tool_result conversion."""

    def test_converts_tool_call_to_model_content(self):
        converter = GeminiHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {"query": "test"}, "tool_call_id": "tc_123"}',
                "message_type": "tool_call",
            }
        ]

        result = converter.convert(raw)
        assert len(result) == 1
        assert result[0].role == "model"
        assert len(result[0].parts) == 1
        assert result[0].parts[0].function_call is not None
        assert result[0].parts[0].function_call.name == "search"
        assert result[0].parts[0].function_call.args == {"query": "test"}
        assert result[0].parts[0].function_call.id == "tc_123"

    def test_converts_tool_result_to_user_content(self):
        converter = GeminiHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {"query": "test"}, "tool_call_id": "tc_123"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "search", "output": "result data", "tool_call_id": "tc_123"}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)
        assert len(result) == 2
        assert result[1].role == "user"
        assert len(result[1].parts) == 1
        function_response = result[1].parts[0].function_response
        assert function_response is not None
        assert function_response.id == "tc_123"
        assert function_response.name == "search"
        assert function_response.response == {"output": "result data"}

    def test_batches_multiple_tool_calls(self):
        converter = GeminiHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "tool1", "args": {}, "tool_call_id": "tc_1"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "args": {}, "tool_call_id": "tc_2"}',
                "message_type": "tool_call",
            },
        ]

        result = converter.convert(raw)
        assert len(result) == 1
        assert result[0].role == "model"
        assert len(result[0].parts) == 2
        assert result[0].parts[0].function_call.name == "tool1"
        assert result[0].parts[1].function_call.name == "tool2"
        assert result[0].parts[0].function_call.id == "tc_1"
        assert result[0].parts[1].function_call.id == "tc_2"

    def test_batches_multiple_tool_results(self):
        converter = GeminiHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "tool1", "args": {}, "tool_call_id": "tc_1"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "args": {}, "tool_call_id": "tc_2"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool1", "output": "result1", "tool_call_id": "tc_1"}',
                "message_type": "tool_result",
            },
            {
                "role": "assistant",
                "content": '{"name": "tool2", "output": "result2", "tool_call_id": "tc_2"}',
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)
        assert len(result) == 2
        assert result[1].role == "user"
        assert len(result[1].parts) == 2
        first_response = result[1].parts[0].function_response
        second_response = result[1].parts[1].function_response
        assert first_response is not None
        assert second_response is not None
        assert first_response.id == "tc_1"
        assert second_response.id == "tc_2"

    def test_formats_tool_errors_as_error_payload(self):
        converter = GeminiHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "search", "output": "boom", "tool_call_id": "tc_1", "is_error": true}',
                "message_type": "tool_result",
            }
        ]

        result = converter.convert(raw)
        assert len(result) == 1
        function_response = result[0].parts[0].function_response
        assert function_response is not None
        assert function_response.response == {"error": "boom"}

    def test_handles_malformed_tool_call_json(self, caplog):
        converter = GeminiHistoryConverter()
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
        converter = GeminiHistoryConverter()
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


class TestTextMessageConversion:
    """Tests for text message conversion."""

    def test_empty_history_returns_empty_list(self):
        converter = GeminiHistoryConverter()
        result = converter.convert([])
        assert result == []

    def test_text_message_with_sender(self):
        converter = GeminiHistoryConverter()
        raw = [
            {
                "role": "user",
                "content": "Hello there",
                "sender_name": "Alice",
                "message_type": "text",
            }
        ]
        result = converter.convert(raw)
        assert len(result) == 1
        assert result[0].role == "user"
        assert result[0].parts[0].text == "[Alice]: Hello there"

    def test_text_message_with_empty_sender_name(self):
        converter = GeminiHistoryConverter()
        raw = [
            {
                "role": "user",
                "content": "Hello there",
                "sender_name": "",
                "message_type": "text",
            }
        ]
        result = converter.convert(raw)
        assert len(result) == 1
        assert result[0].parts[0].text == "Hello there"

    def test_text_message_with_missing_sender_name(self):
        converter = GeminiHistoryConverter()
        raw = [
            {
                "role": "user",
                "content": "Hello there",
                "message_type": "text",
            }
        ]
        result = converter.convert(raw)
        assert len(result) == 1
        assert result[0].parts[0].text == "Hello there"

    def test_own_agent_assistant_messages_become_model_role(self):
        converter = GeminiHistoryConverter(agent_name="MyBot")
        raw = [
            {
                "role": "assistant",
                "content": "I am the bot",
                "sender_name": "MyBot",
                "message_type": "text",
            }
        ]
        result = converter.convert(raw)
        assert len(result) == 1
        assert result[0].role == "model"
        assert result[0].parts[0].text == "I am the bot"

    def test_skips_own_agent_text_inside_tool_turn(self):
        converter = GeminiHistoryConverter(agent_name="MyBot")
        raw = [
            {
                "role": "assistant",
                "content": '{"name": "band_send_message", "args": {"content": "Hello"}, "tool_call_id": "tc_1"}',
                "sender_name": "MyBot",
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": "Hello",
                "sender_name": "MyBot",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": '{"name": "band_send_message", "output": {"status": "sent"}, "tool_call_id": "tc_1"}',
                "sender_name": "MyBot",
                "message_type": "tool_result",
            },
        ]

        result = converter.convert(raw)

        assert len(result) == 2
        assert result[0].role == "model"
        assert result[0].parts[0].function_call is not None
        assert result[0].parts[0].text is None
        assert result[1].role == "user"
        assert result[1].parts[0].function_response is not None

    def test_keeps_other_agent_assistant_messages(self):
        converter = GeminiHistoryConverter(agent_name="MyBot")
        raw = [
            {
                "role": "assistant",
                "content": "I am another bot",
                "sender_name": "OtherBot",
                "message_type": "text",
            }
        ]
        result = converter.convert(raw)
        assert len(result) == 1
        assert result[0].role == "user"
        assert "[OtherBot]:" in result[0].parts[0].text

    def test_skips_thought_messages(self):
        converter = GeminiHistoryConverter()
        raw = [
            {
                "role": "assistant",
                "content": "Thinking...",
                "sender_name": "Bot",
                "message_type": "thought",
            }
        ]
        result = converter.convert(raw)
        assert len(result) == 0

    def test_mixed_text_and_tool_events(self):
        converter = GeminiHistoryConverter()
        raw = [
            {
                "role": "user",
                "content": "Search for cats",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "role": "assistant",
                "content": '{"name": "search", "args": {"query": "cats"}, "tool_call_id": "tc_1"}',
                "message_type": "tool_call",
            },
            {
                "role": "assistant",
                "content": '{"name": "search", "output": "found cats", "tool_call_id": "tc_1"}',
                "message_type": "tool_result",
            },
            {
                "role": "user",
                "content": "Thanks!",
                "sender_name": "Alice",
                "message_type": "text",
            },
        ]
        result = converter.convert(raw)
        # Consecutive user entries (tool_result + text) are merged into one
        assert len(result) == 3
        # text (user) -> tool_call (model) -> tool_result + text (merged user)
        assert result[0].role == "user"
        assert "[Alice]: Search for cats" in result[0].parts[0].text
        assert result[1].role == "model"
        assert result[1].parts[0].function_call is not None
        assert result[2].role == "user"
        assert result[2].parts[0].function_response is not None
        assert "[Alice]: Thanks!" in result[2].parts[1].text
