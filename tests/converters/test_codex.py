"""Tests for CodexHistoryConverter."""

from __future__ import annotations

from band.converters.codex import CodexHistoryConverter
from band.core.types import MessageType
from band.integrations.codex.types import CodexMetadataKey
from tests.adapters.codex.kit import recorded_thread


class TestCodexHistoryConverter:
    """Each room's resumable thread is its newest resume record."""

    def test_empty_history_names_no_thread(self) -> None:
        assert CodexHistoryConverter().convert([]).room_to_thread == {}

    def test_each_room_resumes_its_newest_record(self) -> None:
        raw = [
            recorded_thread("thr-old", "room-1"),
            {"message_type": MessageType.TEXT, "content": "ignored"},
            recorded_thread("thr-other", "room-2"),
            recorded_thread("thr-new", "room-1"),
        ]

        state = CodexHistoryConverter().convert(raw)

        assert state.room_to_thread == {"room-1": "thr-new", "room-2": "thr-other"}

    def test_narration_naming_the_room_and_a_thread_is_no_record(self) -> None:
        narration = {
            "message_type": MessageType.TASK,
            "content": "Codex thread started",
            "metadata": {
                CodexMetadataKey.ROOM: "room-1",
                CodexMetadataKey.THREAD: "thr-unseeded",
            },
        }

        state = CodexHistoryConverter().convert(
            [recorded_thread("thr-1", "room-1"), narration]
        )

        assert state.room_to_thread == {"room-1": "thr-1"}

    def test_the_room_transcript_is_rendered_for_replay(self) -> None:
        raw = [
            recorded_thread("thr-1", "room-1"),
            {"message_type": MessageType.TEXT, "content": "hi", "sender_name": "Alice"},
        ]

        assert CodexHistoryConverter().convert(raw).replay_messages == ["[Alice]: hi"]
