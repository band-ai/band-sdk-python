"""The converter reads each room's resumable session from its resume records."""

from __future__ import annotations

from typing import Any

from band.converters.copilot_sdk import (
    COPILOT_RESUME_RECORD,
    CopilotMetadataKey,
    CopilotSDKHistoryConverter,
)
from band.core.types import MessageType


def resume_record(metadata: dict[str, str]) -> dict[str, Any]:
    return {
        "content": COPILOT_RESUME_RECORD.label,
        "message_type": MessageType.TASK,
        "metadata": metadata,
    }


def alice_says_hello() -> dict[str, Any]:
    return {
        "content": "Hello",
        "sender_name": "Alice",
        "message_type": MessageType.TEXT,
    }


class TestSessionIdExtraction:
    def test_the_newest_record_names_the_rooms_session(self):
        raw = [
            resume_record(
                {CopilotMetadataKey.ROOM: "room-1", CopilotMetadataKey.SESSION: "old"}
            ),
            alice_says_hello(),
            resume_record(
                {CopilotMetadataKey.ROOM: "room-1", CopilotMetadataKey.SESSION: "new"}
            ),
        ]

        result = CopilotSDKHistoryConverter().convert(raw)

        assert result.room_to_session == {"room-1": "new"}

    def test_a_record_without_a_room_is_not_resumable(self):
        """Records written before seeds were confirmed carry no room id."""
        raw = [resume_record({CopilotMetadataKey.SESSION: "unconfirmed"})]

        result = CopilotSDKHistoryConverter().convert(raw)

        assert result.room_to_session == {}

    def test_records_stay_out_of_the_text(self):
        raw = [
            alice_says_hello(),
            resume_record(
                {CopilotMetadataKey.ROOM: "room-1", CopilotMetadataKey.SESSION: "s"}
            ),
        ]

        result = CopilotSDKHistoryConverter().convert(raw)

        assert result.text == "[Alice]: Hello"
