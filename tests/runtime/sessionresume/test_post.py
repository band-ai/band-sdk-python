"""A room's resume record is posted only once its session is seeded."""

from __future__ import annotations

from unittest.mock import ANY

import pytest

from band.core.types import MessageType
from band.runtime.sessionresume import SessionSeed
from band.testing import FakeAgentTools
from tests.runtime.sessionresume.kit import RECORD, confirmed


class TestResumeRecordPost:
    @pytest.mark.parametrize(
        "seed",
        [SessionSeed(), SessionSeed(restored=True)],
        ids=["fresh", "restored"],
    )
    async def test_an_unseeded_session_posts_no_record(self, seed: SessionSeed) -> None:
        tools = FakeAgentTools()

        posted = await RECORD.post(tools, room_id="room-1", seed=seed, session="s1")

        assert posted is False
        assert tools.events_sent == []

    @pytest.mark.parametrize(
        "seed",
        [SessionSeed(), SessionSeed(restored=True)],
        ids=["fresh", "restored"],
    )
    async def test_a_confirmed_session_is_read_back_as_the_rooms_candidate(
        self, seed: SessionSeed
    ) -> None:
        tools = FakeAgentTools()

        posted = await RECORD.post(
            tools, room_id="room-1", seed=confirmed(seed), session="s1"
        )

        assert posted is True
        assert RECORD.candidates(tools.events_sent) == {"room-1": "s1"}

    async def test_a_record_without_a_session_keeps_the_earlier_candidate(
        self,
    ) -> None:
        tools, seed = FakeAgentTools(), confirmed(SessionSeed())
        await RECORD.post(tools, room_id="room-1", seed=seed, session="s1")

        await RECORD.post(tools, room_id="room-1", seed=seed, session=None)

        assert RECORD.candidates(tools.events_sent) == {"room-1": "s1"}

    @pytest.mark.parametrize("empty", ["", None], ids=["empty", "missing"])
    async def test_an_empty_session_is_left_out_of_the_record(
        self, empty: str | None
    ) -> None:
        tools = FakeAgentTools()

        await RECORD.post(
            tools,
            room_id="room-1",
            seed=confirmed(SessionSeed()),
            session=empty,
            details={"extra": "x"},
        )

        assert tools.events_sent == [
            {
                "id": ANY,
                "content": RECORD.label,
                "message_type": MessageType.TASK,
                "metadata": {RECORD.room_key: "room-1", "extra": "x"},
            }
        ]

    async def test_a_failed_post_leaves_the_next_turns_post_to_land(self) -> None:
        tools, seed = FakeAgentTools(), confirmed(SessionSeed())
        tools.send_event_error = ConnectionError("Band REST unavailable")
        failed = await RECORD.post(tools, room_id="room-1", seed=seed, session="s1")

        tools.send_event_error = None
        retried = await RECORD.post(tools, room_id="room-1", seed=seed, session="s1")

        assert (failed, retried) == (False, True)
        assert RECORD.candidates(tools.events_sent) == {"room-1": "s1"}
