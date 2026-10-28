"""A seeded OpenCode session's resume record reaches the room once."""

from __future__ import annotations

from collections.abc import Callable

from band.adapters.opencode import OpencodeAdapter
from band.converters.opencode import OPENCODE_RESUME_RECORD
from tests.adapters.opencode.helpers import FakeOpencodeClient
from tests.adapters.opencode.resume.kit import (
    ROOM_ID,
    TRANSCRIPT,
    answer,
    room_input,
    room_tools,
    started,
)


class TestRecordOnce:
    async def test_later_prompts_in_the_recorded_session_post_no_record(
        self, make_adapter: Callable[..., OpencodeAdapter]
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[
                answer("sess-1", "msg-1"),
                answer("sess-1", "msg-2"),
            ]
        )
        adapter = await started(make_adapter(client, emit=()))
        seeding, later = room_tools(), room_tools()

        await adapter.on_event(room_input(seeding, history=TRANSCRIPT, bootstrap=True))
        await adapter.on_event(room_input(later, history=[], bootstrap=False))

        assert OPENCODE_RESUME_RECORD.candidates(seeding.events_sent) == {
            ROOM_ID: "sess-1"
        }
        assert later.events_sent == []

    async def test_a_record_that_failed_to_post_is_posted_after_the_next_prompt(
        self, make_adapter: Callable[..., OpencodeAdapter]
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[
                answer("sess-1", "msg-1"),
                answer("sess-1", "msg-2"),
            ]
        )
        adapter = await started(make_adapter(client, emit=()))
        rejecting, later = room_tools(), room_tools()
        rejecting.send_event_error = RuntimeError("event rejected")

        await adapter.on_event(
            room_input(rejecting, history=TRANSCRIPT, bootstrap=True)
        )
        await adapter.on_event(room_input(later, history=[], bootstrap=False))

        assert OPENCODE_RESUME_RECORD.candidates(later.events_sent) == {
            ROOM_ID: "sess-1"
        }
