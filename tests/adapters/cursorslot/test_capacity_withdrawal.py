"""A room scheduler cancels its capacity request by its own generation."""

from __future__ import annotations

from tests.adapters.cursorslot.conftest import OpenCursor
from tests.adapters.cursorslot.kit import (
    ROOM_A,
    ROOM_B,
    ROOM_C,
    busy_provider,
    cursor_adapter,
    finish_turn,
    is_accepted,
    line_up,
    occupy,
)

OLD, NEW = 1, 2


class TestCursorCapacityWithdrawal:
    async def test_a_withdrawn_head_does_not_block_a_healthy_room(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        sending = await occupy(session, provider, room=ROOM_A)
        line_up(session, ROOM_B, ROOM_C)

        cursor_adapter(session).withdraw_capacity_wait(room_id=ROOM_B, generation=OLD)
        await finish_turn(provider, sending)

        assert await is_accepted(session, room=ROOM_C)

    async def test_withdrawing_twice_changes_nothing_more(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        sending = await occupy(session, provider, room=ROOM_A)
        line_up(session, ROOM_B, ROOM_C)

        for _ in range(2):
            cursor_adapter(session).withdraw_capacity_wait(
                room_id=ROOM_B, generation=OLD
            )
        await finish_turn(provider, sending)

        assert not await is_accepted(session, room=ROOM_A)
        assert await is_accepted(session, room=ROOM_C)

    async def test_registering_twice_leaves_one_request(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        sending = await occupy(session, provider, room=ROOM_A)
        line_up(session, ROOM_B, ROOM_B, ROOM_C)

        cursor_adapter(session).withdraw_capacity_wait(room_id=ROOM_B, generation=OLD)
        await finish_turn(provider, sending)

        assert await is_accepted(session, room=ROOM_C)

    async def test_a_stale_generations_withdrawal_cannot_cancel_its_replacement(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        sending = await occupy(session, provider, room=ROOM_A)
        line_up(session, ROOM_B, generation=NEW)
        line_up(session, ROOM_C)

        cursor_adapter(session).withdraw_capacity_wait(room_id=ROOM_B, generation=OLD)
        await finish_turn(provider, sending)

        assert not await is_accepted(session, room=ROOM_C)
        assert await is_accepted(session, room=ROOM_B)

    async def test_a_replacement_generation_queues_at_the_tail(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        sending = await occupy(session, provider, room=ROOM_A)
        line_up(session, ROOM_B, generation=OLD)
        line_up(session, ROOM_C)
        line_up(session, ROOM_B, generation=NEW)
        await finish_turn(provider, sending)

        assert not await is_accepted(session, room=ROOM_B)
        assert await is_accepted(session, room=ROOM_C)
        assert await is_accepted(session, room=ROOM_B)

    async def test_a_room_that_registers_again_after_withdrawing_queues_at_the_tail(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        sending = await occupy(session, provider, room=ROOM_A)
        line_up(session, ROOM_B, ROOM_C)

        cursor_adapter(session).withdraw_capacity_wait(room_id=ROOM_B, generation=OLD)
        line_up(session, ROOM_B)
        await finish_turn(provider, sending)

        assert not await is_accepted(session, room=ROOM_B)
        assert await is_accepted(session, room=ROOM_C)
        assert await is_accepted(session, room=ROOM_B)
