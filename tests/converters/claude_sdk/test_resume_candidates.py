"""The converter reads each room's resumable session from its resume records."""

from __future__ import annotations

from typing import Any

from band_rest import ChatMessage
from band_rest.types.chat_message_metadata import ChatMessageMetadata

from band.converters.claude_sdk import (
    CLAUDE_SDK_RESUME_RECORD,
    ClaudeSDKHistoryConverter,
)
from band.runtime.context_serialization import context_item_to_dict

ROOM = "room-1"
ALICE_SAID = {
    "role": "user",
    "content": "Hello",
    "sender_name": "Alice",
    "message_type": "text",
}


def resume_record(session: str) -> dict[str, Any]:
    """The room's resume record for ``session``, as raw history."""
    return {
        "content": CLAUDE_SDK_RESUME_RECORD.label,
        "message_type": "task",
        "metadata": {
            CLAUDE_SDK_RESUME_RECORD.room_key: ROOM,
            CLAUDE_SDK_RESUME_RECORD.session_key: session,
        },
    }


class TestResumeCandidates:
    def test_the_newest_record_names_the_rooms_session(self) -> None:
        raw = [resume_record("sess-old"), ALICE_SAID, resume_record("sess-new")]

        result = ClaudeSDKHistoryConverter().convert(raw)

        assert result.room_to_session == {ROOM: "sess-new"}

    def test_a_record_read_through_a_fern_context_item_names_the_session(
        self,
    ) -> None:
        """``ChatMessage.metadata`` is a frozen Fern model, not a dict, until
        ``context_item_to_dict`` normalizes it."""
        item = ChatMessage(
            id="task-1",
            content=CLAUDE_SDK_RESUME_RECORD.label,
            sender_id="agent-1",
            sender_type="Agent",
            message_type="task",
            metadata=ChatMessageMetadata(
                **{
                    CLAUDE_SDK_RESUME_RECORD.room_key: ROOM,
                    CLAUDE_SDK_RESUME_RECORD.session_key: "sess-abc",
                }
            ),
        )

        result = ClaudeSDKHistoryConverter().convert([context_item_to_dict(item)])

        assert result.room_to_session == {ROOM: "sess-abc"}

    def test_a_session_id_recorded_without_its_room_is_not_resumed(self) -> None:
        """Ids recorded before the seed rule may name a session whose seed
        failed, so they give way to a fresh session and a replay."""
        legacy = {
            "message_type": "task",
            "metadata": {CLAUDE_SDK_RESUME_RECORD.session_key: "sess-legacy"},
        }

        result = ClaudeSDKHistoryConverter().convert([legacy])

        assert result.room_to_session == {}

    def test_resume_records_never_reach_the_text(self) -> None:
        raw = [ALICE_SAID, resume_record("sess-abc")]

        result = ClaudeSDKHistoryConverter().convert(raw)

        assert result.text == "[Alice]: Hello"
