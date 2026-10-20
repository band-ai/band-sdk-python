from __future__ import annotations

from band.runtime.formatters import (
    format_message_for_llm,
)


class TestFormatMessageForLlmWithParticipants:
    def test_replaces_mentions_when_participants_provided(self):
        msg = {
            "sender_type": "User",
            "content": "Hey @[[uuid1]]",
            "sender_name": "Alice",
        }
        participants = [{"id": "uuid1", "handle": "bob"}]
        result = format_message_for_llm(msg, participants)
        assert result["content"] == "Hey @bob"

    def test_works_without_participants(self):
        msg = {"sender_type": "User", "content": "Hello", "sender_name": "Alice"}
        result = format_message_for_llm(msg)
        assert result["content"] == "Hello"

    def test_works_with_none_participants(self):
        msg = {
            "sender_type": "User",
            "content": "Hello @[[uuid]]",
            "sender_name": "Alice",
        }
        result = format_message_for_llm(msg, None)
        assert result["content"] == "Hello @[[uuid]]"
