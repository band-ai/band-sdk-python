"""Tests for LettaHistoryConverter."""

from __future__ import annotations

from band.converters.letta import LettaHistoryConverter


class TestLettaHistoryConverter:
    """Converter should extract latest Letta agent mapping from task events."""

    def test_convert_empty_history(self) -> None:
        converter = LettaHistoryConverter()
        state = converter.convert([])
        assert state.agent_id is None
        assert state.room_id is None
        assert state.conversation_id is None
        assert state.created_at is None

    def test_convert_finds_latest_letta_mapping(self) -> None:
        converter = LettaHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {
                    "letta_agent_id": "agent-old",
                    "letta_room_id": "room-1",
                    "letta_conversation_id": "conv-old",
                    "letta_created_at": "2026-02-17T10:00:00Z",
                },
            },
            {"message_type": "text", "content": "ignored"},
            {
                "message_type": "task",
                "metadata": {
                    "letta_agent_id": "agent-new",
                    "letta_room_id": "room-2",
                    "letta_conversation_id": "conv-new",
                    "letta_created_at": "2026-02-18T12:34:56Z",
                },
            },
        ]

        state = converter.convert(raw_history)
        assert state.agent_id == "agent-new"
        assert state.room_id == "room-2"
        assert state.conversation_id == "conv-new"
        assert state.created_at is not None
        assert state.created_at.year == 2026

    def test_convert_ignores_non_letta_task_metadata(self) -> None:
        converter = LettaHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {"codex_thread_id": "thr-1"},
            },
            {
                "message_type": "task",
                "metadata": {"other_key": "value"},
            },
        ]

        state = converter.convert(raw_history)
        assert state.agent_id is None
        assert state.room_id is None

    def test_convert_handles_none_metadata(self) -> None:
        converter = LettaHistoryConverter()
        raw_history = [
            {"message_type": "task", "metadata": None},
        ]
        state = converter.convert(raw_history)
        assert state.agent_id is None

    def test_convert_handles_non_dict_metadata(self) -> None:
        converter = LettaHistoryConverter()
        raw_history = [
            {"message_type": "task", "metadata": "not-a-dict"},
        ]
        state = converter.convert(raw_history)
        assert state.agent_id is None

    def test_convert_handles_missing_metadata_key(self) -> None:
        converter = LettaHistoryConverter()
        raw_history = [
            {"message_type": "task"},
        ]
        state = converter.convert(raw_history)
        assert state.agent_id is None

    def test_convert_handles_invalid_datetime(self) -> None:
        converter = LettaHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {
                    "letta_agent_id": "agent-1",
                    "letta_room_id": "room-1",
                    "letta_created_at": "not-a-date",
                },
            },
        ]
        state = converter.convert(raw_history)
        assert state.agent_id == "agent-1"
        assert state.created_at is None

    def test_convert_handles_empty_agent_id(self) -> None:
        """Empty string agent_id should be skipped."""
        converter = LettaHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {
                    "letta_agent_id": "",
                    "letta_room_id": "room-1",
                },
            },
        ]
        state = converter.convert(raw_history)
        assert state.agent_id is None

    def test_convert_coerces_integer_agent_id(self) -> None:
        """Non-string agent_id should be coerced to string."""
        converter = LettaHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {
                    "letta_agent_id": 42,
                    "letta_room_id": "room-1",
                },
            },
        ]
        state = converter.convert(raw_history)
        assert state.agent_id == "42"

    def test_convert_returns_first_match_in_reversed_order(self) -> None:
        """When multiple valid entries exist, the last one in the list wins."""
        converter = LettaHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {"letta_agent_id": "agent-old"},
            },
            {
                "message_type": "task",
                "metadata": {"letta_agent_id": "agent-new"},
            },
        ]
        state = converter.convert(raw_history)
        assert state.agent_id == "agent-new"

    def test_set_agent_name_is_noop(self) -> None:
        """LettaHistoryConverter.set_agent_name is a no-op."""
        converter = LettaHistoryConverter()
        converter.set_agent_name("test-agent")  # should not raise

    def test_has_agent_returns_true_when_agent_id_present(self) -> None:
        converter = LettaHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {"letta_agent_id": "agent-123"},
            },
        ]
        state = converter.convert(raw_history)
        assert state.has_agent() is True

    def test_has_agent_returns_false_when_empty(self) -> None:
        converter = LettaHistoryConverter()
        state = converter.convert([])
        assert state.has_agent() is False

    def test_convert_without_conversation_id(self) -> None:
        """conversation_id is optional — should be None if not present."""
        converter = LettaHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {
                    "letta_agent_id": "agent-1",
                    "letta_room_id": "room-1",
                },
            },
        ]
        state = converter.convert(raw_history)
        assert state.agent_id == "agent-1"
        assert state.conversation_id is None
