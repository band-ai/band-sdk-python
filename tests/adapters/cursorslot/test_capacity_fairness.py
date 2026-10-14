"""Cursor hands a free provider slot to the room that has waited longest."""

from __future__ import annotations

from tests.adapters.cursorslot.conftest import OpenCursor
from tests.adapters.cursorslot.kit import (
    ROOM_A,
    ROOM_B,
    ROOM_C,
    busy_provider,
    finish_turn,
    is_accepted,
    line_up,
    occupy,
)
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent


class TestCursorCapacityFairness:
    async def test_a_free_slot_with_nobody_waiting_is_taken_at_once(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        await finish_turn(provider, await occupy(session, provider))

        assert await is_accepted(session, room=ROOM_B)

    async def test_the_room_that_just_finished_joins_behind_a_waiting_room(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        sending = await occupy(session, provider, room=ROOM_A)
        line_up(session, ROOM_B)
        await finish_turn(provider, sending)

        assert not await is_accepted(session, room=ROOM_A)
        assert await is_accepted(session, room=ROOM_B)

    async def test_continuous_traffic_in_one_room_cannot_bypass_a_waiter(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        sending = await occupy(session, provider, room=ROOM_A)
        line_up(session, ROOM_B)
        await finish_turn(provider, sending)

        # A's scheduler queues behind B and keeps retrying while the slot is free.
        line_up(session, ROOM_A)
        refusals = [await is_accepted(session, room=ROOM_A) for _ in range(3)]

        assert refusals == [False, False, False]
        assert await is_accepted(session, room=ROOM_B)
        assert await is_accepted(session, room=ROOM_A)

    async def test_a_waiters_place_survives_repeated_refusals_and_re_registration(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        sending = await occupy(session, provider, room=ROOM_A)
        line_up(session, ROOM_B, ROOM_C)

        for _ in range(3):
            assert not await is_accepted(session, room=ROOM_B)
            line_up(session, ROOM_B)
        await finish_turn(provider, sending)

        assert not await is_accepted(session, room=ROOM_C)
        assert await is_accepted(session, room=ROOM_B)
        assert await is_accepted(session, room=ROOM_C)

    async def test_a_room_that_took_the_slot_no_longer_holds_up_the_line(
        self, open_cursor: OpenCursor
    ) -> None:
        session = await open_cursor(FakeACPAgent().will_say("ok"))
        line_up(session, ROOM_B)

        assert await is_accepted(session, room=ROOM_B)

        assert await is_accepted(session, room=ROOM_C)
