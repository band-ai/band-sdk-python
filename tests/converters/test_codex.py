"""Tests for CodexHistoryConverter."""

from __future__ import annotations

from band.converters.codex import CodexHistoryConverter


class TestCodexHistoryConverter:
    """Converter should extract latest codex thread mapping from event metadata."""

    def test_convert_empty_history(self) -> None:
        converter = CodexHistoryConverter()
        state = converter.convert([])
        assert state.thread_id is None
        assert state.room_id is None
        assert state.created_at is None

    def test_convert_finds_latest_codex_mapping(self) -> None:
        converter = CodexHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {
                    "codex_thread_id": "thr_old",
                    "codex_room_id": "room-1",
                    "codex_created_at": "2026-02-17T10:00:00Z",
                },
            },
            {"message_type": "text", "content": "ignored"},
            {
                "message_type": "task",
                "metadata": {
                    "codex_thread_id": "thr_new",
                    "codex_room_id": "room-2",
                    "codex_created_at": "2026-02-18T12:34:56Z",
                },
            },
        ]

        state = converter.convert(raw_history)
        assert state.thread_id == "thr_new"
        assert state.room_id == "room-2"
        assert state.created_at is not None
        assert state.created_at.year == 2026

    def test_convert_reads_non_task_thread_mapping_metadata(self) -> None:
        converter = CodexHistoryConverter()
        raw_history = [
            {
                "message_type": "tool_result",
                "content": "Codex thread mapped",
                "metadata": {
                    "codex_event_type": "thread_mapping",
                    "codex_thread_id": "thr-tool-result",
                    "codex_room_id": "room-3",
                    "codex_created_at": "2026-02-19T01:02:03Z",
                },
            },
        ]

        state = converter.convert(raw_history)
        assert state.thread_id == "thr-tool-result"
        assert state.room_id == "room-3"
        assert state.created_at is not None

    def test_convert_ignores_non_codex_metadata(self) -> None:
        converter = CodexHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {"a2a_task_id": "task-1"},
            },
            {
                "message_type": "task",
                "metadata": {"other_key": "value"},
            },
        ]

        state = converter.convert(raw_history)
        assert state.thread_id is None
        assert state.room_id is None

    def test_convert_handles_none_metadata(self) -> None:
        converter = CodexHistoryConverter()
        raw_history = [
            {"message_type": "task", "metadata": None},
        ]
        state = converter.convert(raw_history)
        assert state.thread_id is None

    def test_convert_handles_non_dict_metadata(self) -> None:
        converter = CodexHistoryConverter()
        raw_history = [
            {"message_type": "task", "metadata": "not-a-dict"},
        ]
        state = converter.convert(raw_history)
        assert state.thread_id is None

    def test_convert_handles_missing_metadata_key(self) -> None:
        converter = CodexHistoryConverter()
        raw_history = [
            {"message_type": "task"},
        ]
        state = converter.convert(raw_history)
        assert state.thread_id is None

    def test_convert_handles_invalid_datetime(self) -> None:
        converter = CodexHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {
                    "codex_thread_id": "thr-1",
                    "codex_room_id": "room-1",
                    "codex_created_at": "not-a-date",
                },
            },
        ]
        state = converter.convert(raw_history)
        assert state.thread_id == "thr-1"
        assert state.created_at is None

    def test_convert_handles_empty_thread_id(self) -> None:
        """Empty string thread_id should be skipped."""
        converter = CodexHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {
                    "codex_thread_id": "",
                    "codex_room_id": "room-1",
                },
            },
        ]
        state = converter.convert(raw_history)
        assert state.thread_id is None

    def test_convert_coerces_integer_thread_id(self) -> None:
        """Non-string thread_id should be coerced to string."""
        converter = CodexHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {
                    "codex_thread_id": 42,
                    "codex_room_id": "room-1",
                },
            },
        ]
        state = converter.convert(raw_history)
        assert state.thread_id == "42"

    def test_convert_returns_first_match_in_reversed_order(self) -> None:
        """When multiple valid entries exist, the last one in the list wins."""
        converter = CodexHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {"codex_thread_id": "thr-old"},
            },
            {
                "message_type": "task",
                "metadata": {"codex_thread_id": "thr-new"},
            },
        ]
        state = converter.convert(raw_history)
        assert state.thread_id == "thr-new"

    def test_set_agent_name_is_noop(self) -> None:
        """CodexHistoryConverter.set_agent_name is a no-op (metadata-only converter)."""
        converter = CodexHistoryConverter()
        converter.set_agent_name("test-agent")  # should not raise
