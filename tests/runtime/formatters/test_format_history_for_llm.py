from __future__ import annotations

from band.runtime.formatters import (
    format_history_for_llm,
)


class TestFormatHistoryForLlm:
    def test_formats_multiple_messages(self):
        messages = [
            {"id": "1", "sender_type": "User", "content": "Hi", "sender_name": "Alice"},
            {
                "id": "2",
                "sender_type": "Agent",
                "content": "Hello",
                "sender_name": "Bot",
            },
        ]
        result = format_history_for_llm(messages)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_excludes_message_by_id(self):
        messages = [
            {"id": "1", "content": "First", "sender_type": "User"},
            {"id": "2", "content": "Second", "sender_type": "User"},
        ]
        result = format_history_for_llm(messages, exclude_id="1")
        assert len(result) == 1
        assert result[0]["content"] == "Second"

    def test_empty_list(self):
        result = format_history_for_llm([])
        assert result == []

    def test_none_exclude_id_includes_all(self):
        messages = [
            {"id": "1", "content": "First", "sender_type": "User"},
            {"id": "2", "content": "Second", "sender_type": "User"},
        ]
        result = format_history_for_llm(messages, exclude_id=None)
        assert len(result) == 2
