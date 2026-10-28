"""Room history yields each room's newest recorded session."""

from __future__ import annotations

import pytest

from band.core.types import MessageType
from tests.runtime.sessionresume.kit import RECORD, record, session


class TestResumeCandidates:
    def test_the_newest_record_wins_per_room(self) -> None:
        history = [
            record("room-1", **session("old")),
            record("room-2", **session("other")),
            record("room-1", **session("new")),
        ]

        assert RECORD.candidates(history) == {"room-1": "new", "room-2": "other"}

    def test_a_record_without_a_session_keeps_the_candidate(self) -> None:
        history = [record("room-1", **session("saved")), record("room-1", extra="x")]

        assert RECORD.candidates(history) == {"room-1": "saved"}

    @pytest.mark.parametrize("cleared", ["", None, 7], ids=["empty", "null", "int"])
    def test_an_unusable_session_clears_the_candidate(self, cleared: object) -> None:
        history = [
            record("room-1", **session("saved")),
            record("room-1", **session(cleared)),
        ]

        assert RECORD.candidates(history) == {}

    def test_only_task_events_are_records(self) -> None:
        history = [
            record("room-1", message_type=MessageType.TEXT, **session("spoofed"))
        ]

        assert RECORD.candidates(history) == {}

    def test_unusable_metadata_is_ignored(self) -> None:
        history = [
            {"message_type": MessageType.TASK, "metadata": "not a mapping"},
            {"message_type": MessageType.TASK, "metadata": None},
            record(7, **session("no room")),
        ]

        assert RECORD.candidates(history) == {}
