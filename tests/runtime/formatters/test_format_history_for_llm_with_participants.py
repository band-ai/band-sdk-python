from __future__ import annotations

from band.runtime.formatters import (
    format_history_for_llm,
)


class TestFormatHistoryForLlmWithParticipants:
    def test_replaces_mentions_in_history(self):
        messages = [
            {
                "id": "1",
                "sender_type": "User",
                "content": "Hey @[[uuid1]]",
                "sender_name": "Alice",
            },
            {
                "id": "2",
                "sender_type": "Agent",
                "content": "Hi @[[uuid2]]",
                "sender_name": "Bot",
            },
        ]
        participants = [
            {"id": "uuid1", "handle": "bob"},
            {"id": "uuid2", "handle": "alice"},
        ]
        result = format_history_for_llm(messages, participants=participants)
        assert result[0]["content"] == "Hey @bob"
        assert result[1]["content"] == "Hi @alice"

    def test_works_without_participants(self):
        messages = [
            {"id": "1", "sender_type": "User", "content": "Hello", "sender_name": "A"}
        ]
        result = format_history_for_llm(messages)
        assert result[0]["content"] == "Hello"
