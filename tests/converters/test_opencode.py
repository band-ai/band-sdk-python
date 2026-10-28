"""OpencodeHistoryConverter reads each room's resumable session and transcript."""

from __future__ import annotations

from band.converters.opencode import OpencodeHistoryConverter, OpencodeMetadataKey
from band.integrations.opencode.types import OpencodeSessionState


def _record(room: str, session: str, registration: str | None = None) -> dict:
    metadata = {OpencodeMetadataKey.ROOM: room, OpencodeMetadataKey.SESSION: session}
    if registration is not None:
        metadata[OpencodeMetadataKey.MCP_SERVER_NAME] = registration
    return {"message_type": "task", "metadata": metadata}


class TestOpencodeHistoryConverter:
    def test_each_room_resumes_its_newest_recorded_session(self) -> None:
        state = OpencodeHistoryConverter().convert(
            [
                _record("room-1", "sess-old", "band_agent"),
                _record("room-2", "sess-other"),
                _record("room-1", "sess-new", "band_agent"),
            ]
        )

        assert state == OpencodeSessionState(
            room_to_session={"room-1": "sess-new", "room-2": "sess-other"},
            session_registrations={"sess-old": "band_agent", "sess-new": "band_agent"},
        )

    def test_unrelated_and_malformed_task_events_name_no_session(self) -> None:
        state = OpencodeHistoryConverter().convert(
            [
                {"message_type": "task", "metadata": "not-a-dict"},
                {"message_type": "task", "metadata": {"codex_thread_id": "thr-1"}},
            ]
        )

        assert state == OpencodeSessionState()

    def test_text_history_becomes_the_replay_transcript(self) -> None:
        state = OpencodeHistoryConverter().convert(
            [
                {
                    "message_type": "text",
                    "content": "hello",
                    "sender_name": "Alice",
                    "sender_type": "User",
                },
                {"content": "hi there", "sender_name": "Bob", "sender_type": "User"},
                _record("room-1", "sess-1"),
            ]
        )

        assert state.replay_messages == ["[Alice]: hello", "[Bob]: hi there"]
