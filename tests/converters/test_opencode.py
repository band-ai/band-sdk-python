"""Tests for OpencodeHistoryConverter."""

from __future__ import annotations

from band.converters.opencode import OpencodeHistoryConverter


class TestOpencodeHistoryConverter:
    def test_convert_empty_history(self) -> None:
        converter = OpencodeHistoryConverter()
        state = converter.convert([])
        assert state.session_id is None
        assert state.room_id is None
        assert state.created_at is None
        assert state.replay_messages == []

    def test_convert_finds_latest_mapping(self) -> None:
        converter = OpencodeHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {
                    "opencode_session_id": "sess-old",
                    "opencode_room_id": "room-1",
                    "opencode_created_at": "2026-03-01T10:00:00Z",
                },
            },
            {
                "message_type": "task",
                "metadata": {
                    "opencode_session_id": "sess-new",
                    "opencode_room_id": "room-2",
                    "opencode_created_at": "2026-03-02T12:34:56Z",
                },
            },
        ]

        state = converter.convert(raw_history)
        assert state.session_id == "sess-new"
        assert state.room_id == "room-2"
        assert state.created_at is not None
        assert state.created_at.year == 2026
        assert state.replay_messages == []

    def test_convert_ignores_non_dict_metadata(self) -> None:
        converter = OpencodeHistoryConverter()
        state = converter.convert([{"message_type": "task", "metadata": "not-a-dict"}])
        assert state.session_id is None

    def test_convert_ignores_unrelated_task_events(self) -> None:
        converter = OpencodeHistoryConverter()
        state = converter.convert(
            [{"message_type": "task", "metadata": {"codex_thread_id": "thr-1"}}]
        )
        assert state.session_id is None

    def test_set_agent_name_is_noop(self) -> None:
        converter = OpencodeHistoryConverter()
        converter.set_agent_name("ignored")
        # Converter still works after calling set_agent_name
        state = converter.convert([])
        assert state.session_id is None
        assert state.replay_messages == []

    def test_convert_collects_replayable_text_history(self) -> None:
        converter = OpencodeHistoryConverter()
        state = converter.convert(
            [
                {
                    "message_type": "text",
                    "content": "hello",
                    "sender_name": "Alice",
                    "sender_type": "User",
                },
                {
                    "message_type": "text",
                    "content": "hi there",
                    "sender_name": "OpenCode Agent",
                    "sender_type": "Agent",
                },
                {
                    "message_type": "text",
                    "content": "   ",
                    "sender_name": "Noisy Sender",
                    "sender_type": "User",
                },
                {
                    "message_type": "task",
                    "metadata": {"opencode_session_id": "sess-1"},
                },
            ]
        )

        assert state.replay_messages == [
            "[Alice]: hello",
            "[OpenCode Agent]: hi there",
        ]
