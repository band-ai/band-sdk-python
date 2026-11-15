"""One parser: whatever recognition calls a control the handler consumes, and
whatever it calls ordinary the handler leaves for the model."""

from __future__ import annotations

import pytest

from tests.adapters.opencode.conftest import ParkRoom
from tests.adapters.opencode.helpers import (
    PERMISSION_ASK,
    QUESTION_ASK,
    TWO_QUESTIONS_ASK,
    RawOpencodeEvent,
    client_asking,
    event_message_updated,
    event_session_idle,
    event_text_part,
    handle_control,
    room_reply,
)

STATES: dict[str, list[RawOpencodeEvent]] = {
    "nothing pending": [
        event_message_updated("sess-1", "msg-1"),
        event_text_part("sess-1", "msg-1", "All done."),
        event_session_idle("sess-1"),
    ],
    "approval": [PERMISSION_ASK],
    "question": [QUESTION_ASK],
    "two questions": [TWO_QUESTIONS_ASK],
    "approval and question": [PERMISSION_ASK, QUESTION_ASK],
}
CONTENTS = [
    "approve",
    "approve req-1",
    "always",
    "reject",
    "reject req-1",
    "reject q-1",
    "blue",
    "line one\nline two",
    "@agent approve",
    "approve please",
    "hello there",
    "",
]


class TestControlRecognitionAgreement:
    @pytest.mark.parametrize("content", CONTENTS)
    @pytest.mark.parametrize("state", STATES)
    async def test_recognition_matches_what_the_handler_consumes(
        self, park_room: ParkRoom, state: str, content: str
    ) -> None:
        adapter = await park_room(client_asking(*STATES[state]))
        message = room_reply(content)

        recognized = adapter.is_control_message(message)

        assert recognized is await handle_control(adapter, message)
