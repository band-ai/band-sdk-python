"""Tests for OmpHistoryConverter."""

from __future__ import annotations

from band.converters.omp import (
    OMP_ROOM_METADATA_KEY,
    OMP_SESSION_METADATA_KEY,
    OmpHistoryConverter,
)


class TestOmpHistoryConverter:
    def test_convert_empty_history(self) -> None:
        state = OmpHistoryConverter().convert([])
        assert state.room_to_session == {}
        assert state.replay_messages == []

    def test_latest_session_file_wins_per_room(self) -> None:
        state = OmpHistoryConverter().convert(
            [
                {
                    "message_type": "task",
                    "metadata": {
                        OMP_ROOM_METADATA_KEY: "room-1",
                        OMP_SESSION_METADATA_KEY: "/sessions/old.jsonl",
                    },
                },
                {
                    "message_type": "task",
                    "metadata": {
                        OMP_ROOM_METADATA_KEY: "room-2",
                        OMP_SESSION_METADATA_KEY: "/sessions/other.jsonl",
                    },
                },
                {
                    "message_type": "task",
                    "metadata": {
                        OMP_ROOM_METADATA_KEY: "room-1",
                        OMP_SESSION_METADATA_KEY: "/sessions/new.jsonl",
                    },
                },
            ]
        )
        assert state.room_to_session == {
            "room-1": "/sessions/new.jsonl",
            "room-2": "/sessions/other.jsonl",
        }

    def test_unusable_metadata_is_ignored(self) -> None:
        state = OmpHistoryConverter().convert(
            [
                {"message_type": "task", "metadata": "not a mapping"},
                {
                    "message_type": "task",
                    "metadata": {OMP_ROOM_METADATA_KEY: "room-1"},
                },
                {
                    "message_type": "task",
                    "metadata": {
                        OMP_ROOM_METADATA_KEY: "room-1",
                        OMP_SESSION_METADATA_KEY: "",
                    },
                },
                {
                    "message_type": "task",
                    "metadata": {
                        OMP_ROOM_METADATA_KEY: 7,
                        OMP_SESSION_METADATA_KEY: "/sessions/x.jsonl",
                    },
                },
            ]
        )
        assert state.room_to_session == {}

    def test_room_transcript_is_replayed(self) -> None:
        state = OmpHistoryConverter().convert(
            [
                {
                    "message_type": "text",
                    "content": "hello",
                    "sender": {"name": "Alice", "type": "User"},
                }
            ]
        )
        assert len(state.replay_messages) == 1
        assert "hello" in state.replay_messages[0]
