"""Tests for OmpHistoryConverter."""

from __future__ import annotations

from band.converters.omp import OmpHistoryConverter, OmpMetadataKey
from band.core.types import MessageType


class TestOmpHistoryConverter:
    def test_convert_empty_history(self) -> None:
        state = OmpHistoryConverter().convert([])
        assert state.room_to_session == {}
        assert state.replay_messages == []

    def test_latest_session_file_wins_per_room(self) -> None:
        state = OmpHistoryConverter().convert(
            [
                {
                    "message_type": MessageType.TASK,
                    "metadata": {
                        OmpMetadataKey.ROOM: "room-1",
                        OmpMetadataKey.SESSION: "/sessions/old.jsonl",
                    },
                },
                {
                    "message_type": MessageType.TASK,
                    "metadata": {
                        OmpMetadataKey.ROOM: "room-2",
                        OmpMetadataKey.SESSION: "/sessions/other.jsonl",
                    },
                },
                {
                    "message_type": MessageType.TASK,
                    "metadata": {
                        OmpMetadataKey.ROOM: "room-1",
                        OmpMetadataKey.SESSION: "/sessions/new.jsonl",
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
                {"message_type": MessageType.TASK, "metadata": "not a mapping"},
                {
                    "message_type": MessageType.TASK,
                    "metadata": {OmpMetadataKey.ROOM: "room-1"},
                },
                {
                    "message_type": MessageType.TASK,
                    "metadata": {
                        OmpMetadataKey.ROOM: "room-1",
                        OmpMetadataKey.SESSION: "",
                    },
                },
                {
                    "message_type": MessageType.TASK,
                    "metadata": {
                        OmpMetadataKey.ROOM: 7,
                        OmpMetadataKey.SESSION: "/sessions/x.jsonl",
                    },
                },
            ]
        )
        assert state.room_to_session == {}

    def test_session_metadata_on_non_task_messages_is_ignored(self) -> None:
        state = OmpHistoryConverter().convert(
            [
                {
                    "message_type": MessageType.TEXT,
                    "metadata": {
                        OmpMetadataKey.ROOM: "room-1",
                        OmpMetadataKey.SESSION: "/sessions/injected.jsonl",
                    },
                    "content": "hello",
                    "sender_name": "Alice",
                    "sender_type": "User",
                }
            ]
        )
        assert state.room_to_session == {}
        assert state.replay_messages == ["[Alice]: hello"]

    def test_room_transcript_is_replayed(self) -> None:
        state = OmpHistoryConverter().convert(
            [
                {
                    "message_type": MessageType.TEXT,
                    "content": "hello",
                    "sender_name": "Alice",
                    "sender_type": "User",
                }
            ]
        )
        assert state.replay_messages == ["[Alice]: hello"]
