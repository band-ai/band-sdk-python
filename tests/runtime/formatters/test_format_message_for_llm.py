from __future__ import annotations

from band.runtime.formatters import (
    format_message_for_llm,
)


class TestFormatMessageForLlm:
    def test_agent_sender_maps_to_assistant(self):
        msg = {"sender_type": "Agent", "content": "Hello", "sender_name": "Bot"}
        result = format_message_for_llm(msg)
        assert result["role"] == "assistant"
        assert result["content"] == "Hello"
        assert result["sender_name"] == "Bot"

    def test_user_sender_maps_to_user(self):
        msg = {"sender_type": "User", "content": "Hi", "sender_name": "Alice"}
        result = format_message_for_llm(msg)
        assert result["role"] == "user"

    def test_unknown_sender_maps_to_user(self):
        msg = {"sender_type": "", "content": "Test"}
        result = format_message_for_llm(msg)
        assert result["role"] == "user"

    def test_fallback_sender_name_to_type(self):
        # Falls back to sender_type if no name
        msg = {"sender_type": "Agent", "content": ""}
        result = format_message_for_llm(msg)
        assert result["sender_name"] == "Agent"

    def test_fallback_sender_name_to_name_field(self):
        # Falls back to "name" field if sender_name missing
        msg = {"sender_type": "User", "content": "Hi", "name": "Bob"}
        result = format_message_for_llm(msg)
        assert result["sender_name"] == "Bob"

    def test_includes_sender_type(self):
        msg = {"sender_type": "Agent", "content": "Test", "sender_name": "Bot"}
        result = format_message_for_llm(msg)
        assert result["sender_type"] == "Agent"

    def test_preserves_message_type(self):
        # text message
        msg = {"sender_type": "Agent", "content": "Hello", "message_type": "text"}
        result = format_message_for_llm(msg)
        assert result["message_type"] == "text"

        # tool_call message
        msg = {"sender_type": "Agent", "content": "{...}", "message_type": "tool_call"}
        result = format_message_for_llm(msg)
        assert result["message_type"] == "tool_call"

        # tool_result message
        msg = {
            "sender_type": "Agent",
            "content": "{...}",
            "message_type": "tool_result",
        }
        result = format_message_for_llm(msg)
        assert result["message_type"] == "tool_result"

        # thought message
        msg = {
            "sender_type": "Agent",
            "content": "thinking...",
            "message_type": "thought",
        }
        result = format_message_for_llm(msg)
        assert result["message_type"] == "thought"

    def test_defaults_message_type_to_text(self):
        # Missing message_type defaults to "text"
        msg = {"sender_type": "Agent", "content": "Hello"}
        result = format_message_for_llm(msg)
        assert result["message_type"] == "text"

    def test_preserves_metadata(self):
        """Should preserve metadata for adapters that need it (e.g., A2A)."""
        msg = {
            "sender_type": "Agent",
            "content": "A2A task completed",
            "message_type": "task",
            "metadata": {
                "a2a_context_id": "ctx-123",
                "a2a_task_id": "task-456",
                "a2a_task_state": "completed",
            },
        }
        result = format_message_for_llm(msg)
        assert result["metadata"] == {
            "a2a_context_id": "ctx-123",
            "a2a_task_id": "task-456",
            "a2a_task_state": "completed",
        }

    def test_defaults_metadata_to_empty_dict(self):
        """Should default metadata to empty dict if missing."""
        msg = {"sender_type": "Agent", "content": "Hello"}
        result = format_message_for_llm(msg)
        assert result["metadata"] == {}
