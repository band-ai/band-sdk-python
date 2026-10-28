"""A fresh OpenCode session whose seeding prompt fails is never resumed."""

from __future__ import annotations

from collections.abc import Callable

from band.adapters.opencode import OpencodeAdapter
from band.converters.opencode import OPENCODE_RESUME_RECORD
from tests.adapters.opencode.helpers import FakeOpencodeClient, event_session_error
from tests.adapters.opencode.resume.kit import (
    ROOM_ID,
    TRANSCRIPT,
    answer,
    prompts,
    room_input,
    room_tools,
    started,
)


class TestFailedSeed:
    async def test_the_next_turn_seeds_a_new_session_and_only_it_is_recorded(
        self, make_adapter: Callable[..., OpencodeAdapter]
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_session_error("sess-1", "provider unavailable")],
                answer("sess-2", "msg-2"),
            ]
        )
        adapter = await started(make_adapter(client, emit=()))
        failed, retried = room_tools(), room_tools()

        await adapter.on_event(room_input(failed, history=TRANSCRIPT, bootstrap=True))
        await adapter.on_event(room_input(retried, history=TRANSCRIPT, bootstrap=False))

        assert prompts(client) == [("sess-1", True), ("sess-2", True)]
        assert OPENCODE_RESUME_RECORD.candidates(failed.events_sent) == {}
        assert OPENCODE_RESUME_RECORD.candidates(retried.events_sent) == {
            ROOM_ID: "sess-2"
        }
