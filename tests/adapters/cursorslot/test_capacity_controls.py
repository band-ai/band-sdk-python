"""Room controls never join, wait on or consume a place in Cursor's capacity line."""

from __future__ import annotations

from band.integrations.acp.cursor import ROOM_COMMAND, CursorCommandWord
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

LIST_DECISIONS = f"{ROOM_COMMAND} {CursorCommandWord.DECISIONS}"


class TestCursorCapacityControls:
    async def test_a_control_is_handled_while_the_slot_is_busy_and_rooms_wait(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        sending = await occupy(session, provider, room=ROOM_A)
        line_up(session, ROOM_B)

        reply = await session.send(LIST_DECISIONS, room=ROOM_C)
        await finish_turn(provider, sending)

        assert reply.texts == ["Pending Cursor decisions: none"]
        assert len(provider.agent.prompts) == 1
        assert not await is_accepted(session, room=ROOM_C)
        assert await is_accepted(session, room=ROOM_B)

    async def test_a_control_from_the_head_room_keeps_its_place_and_the_free_slot(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        sending = await occupy(session, provider, room=ROOM_A)
        line_up(session, ROOM_B, ROOM_C)
        await finish_turn(provider, sending)

        await session.send(LIST_DECISIONS, room=ROOM_B)

        assert not await is_accepted(session, room=ROOM_C)
        assert await is_accepted(session, room=ROOM_B)
