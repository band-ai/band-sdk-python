"""Tests for CodexHistoryConverter."""

from __future__ import annotations

from typing import Any

from band.converters.codex import CodexHistoryConverter, CodexMetadataKey
from band.core.types import MessageType


def record(room: str, thread: str) -> dict[str, Any]:
    return {
        "message_type": MessageType.TASK,
        "metadata": {CodexMetadataKey.ROOM: room, CodexMetadataKey.THREAD: thread},
    }


class TestCodexHistoryConverter:
    """Each room's resumable thread is its newest resume record."""

    def test_empty_history_names_no_thread(self) -> None:
        assert CodexHistoryConverter().convert([]).room_to_thread == {}

    def test_each_room_resumes_its_newest_record(self) -> None:
        raw = [
            record("room-1", "thr-old"),
            {"message_type": MessageType.TEXT, "content": "ignored"},
            record("room-2", "thr-other"),
            record("room-1", "thr-new"),
        ]

        state = CodexHistoryConverter().convert(raw)

        assert state.room_to_thread == {"room-1": "thr-new", "room-2": "thr-other"}

    def test_narration_naming_only_a_thread_is_no_record(self) -> None:
        narration = {
            "message_type": MessageType.TASK,
            "metadata": {CodexMetadataKey.THREAD: "thr-unseeded"},
        }

        state = CodexHistoryConverter().convert([record("room-1", "thr-1"), narration])

        assert state.room_to_thread == {"room-1": "thr-1"}
