"""Unit tests for pure formatting functions."""

from __future__ import annotations

from datetime import datetime, timezone

from band.runtime.formatters import (
    format_message_for_llm,
    format_history_for_llm,
    build_participants_message,
    replace_uuid_mentions,
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

    def test_sorts_datetime_timestamps_chronologically(self):
        messages = [
            {
                "id": "2",
                "content": "Second",
                "sender_type": "User",
                "inserted_at": datetime(2024, 1, 1, 0, 0, 2, tzinfo=timezone.utc),
            },
            {
                "id": "1",
                "content": "First",
                "sender_type": "User",
                "inserted_at": datetime(2024, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
            },
        ]

        result = format_history_for_llm(messages)

        assert [message["content"] for message in result] == ["First", "Second"]

    def test_untimestamped_messages_keep_relative_position(self):
        messages = [
            {
                "id": "1",
                "content": "First",
                "sender_type": "User",
                "inserted_at": "2024-01-01T00:00:01Z",
            },
            {"id": "injected", "content": "Injected", "sender_type": "User"},
            {
                "id": "2",
                "content": "Second",
                "sender_type": "User",
                "inserted_at": "2024-01-01T00:00:02Z",
            },
        ]

        result = format_history_for_llm(messages)

        assert [message["content"] for message in result] == [
            "First",
            "Injected",
            "Second",
        ]


class TestBuildParticipantsMessage:
    def test_empty_participants(self):
        result = build_participants_message([])
        assert "No other participants" in result

    def test_formats_participants(self):
        participants = [
            {"id": "u1", "name": "Alice", "type": "User"},
            {"id": "a1", "name": "Bot", "type": "Agent"},
        ]
        result = build_participants_message(participants)
        assert "Alice" in result
        assert "Bot" in result
        # IDs are intentionally NOT shown to prevent LLM from using them in mentions
        assert "u1" not in result
        assert "User" in result

    def test_includes_mention_instruction(self):
        participants = [{"id": "1", "name": "Test", "type": "User", "handle": "test"}]
        result = build_participants_message(participants)
        assert "band_send_message" in result
        # Instruction emphasizes using exact handles, not display names
        assert "handle" in result
        assert "NOT the display name" in result

    def test_includes_participant_descriptions_when_available(self):
        participants = [
            {
                "id": "a1",
                "name": "Greeter",
                "type": "Agent",
                "handle": "darvell/greeter",
                "description": "Writes greeting-card copy for named recipients.",
            }
        ]
        result = build_participants_message(participants)
        assert (
            "@darvell/greeter — Greeter (Agent): "
            "Writes greeting-card copy for named recipients."
        ) in result

    def test_handles_missing_fields(self):
        participants = [{"id": "1"}]  # Missing name and type
        result = build_participants_message(participants)
        assert "Unknown" in result  # Default for missing name/type


class TestReplaceUuidMentions:
    def test_replaces_single_uuid_mention(self):
        content = "Hey @[[550e8400-e29b-41d4-a716-446655440000]], check this"
        participants = [
            {"id": "550e8400-e29b-41d4-a716-446655440000", "handle": "john"}
        ]
        result = replace_uuid_mentions(content, participants)
        assert result == "Hey @john, check this"

    def test_replaces_multiple_uuid_mentions(self):
        content = "Hi @[[uuid1]] and @[[uuid2]]"
        participants = [
            {"id": "uuid1", "handle": "alice"},
            {"id": "uuid2", "handle": "bob"},
        ]
        result = replace_uuid_mentions(content, participants)
        assert result == "Hi @alice and @bob"

    def test_preserves_content_when_no_participants(self):
        content = "Hello @[[some-uuid]]"
        result = replace_uuid_mentions(content, [])
        assert result == "Hello @[[some-uuid]]"

    def test_preserves_unmatched_uuids(self):
        content = "@[[unknown-uuid]] hello"
        participants = [{"id": "different-uuid", "handle": "john"}]
        result = replace_uuid_mentions(content, participants)
        assert result == "@[[unknown-uuid]] hello"

    def test_handles_missing_handle(self):
        content = "@[[uuid1]] hello"
        participants = [{"id": "uuid1", "name": "John"}]  # No handle
        result = replace_uuid_mentions(content, participants)
        assert result == "@[[uuid1]] hello"  # Preserved

    def test_handles_empty_content(self):
        result = replace_uuid_mentions("", [{"id": "uuid1", "handle": "john"}])
        assert result == ""

    def test_handles_none_participants(self):
        # Verify behavior when participants is falsy
        content = "Hello @[[uuid1]]"
        result = replace_uuid_mentions(content, [])
        assert result == "Hello @[[uuid1]]"


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
