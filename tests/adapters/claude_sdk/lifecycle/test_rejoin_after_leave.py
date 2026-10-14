"""A room left mid-turn is served again on rejoin."""

from __future__ import annotations

import asyncio

import pytest

from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import NO_SETTLE_GRACE, OpenRoom, said


class TestRejoinAfterLeave:
    async def test_a_room_left_mid_turn_can_be_rejoined_with_a_fresh_session(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(NO_SETTLE_GRACE)
        thinking = Hold()
        room.claude.script(
            [thinking, room.model_reply("never sent")],
            [room.model_reply("welcome back")],
        )
        message = asyncio.create_task(room.send("long job"))
        async with thinking:
            await room.leave()
        with pytest.raises(asyncio.CancelledError):
            await message

        await room.send("I'm back", history=(said("Bob", "long job"),))

        assert len(room.claude.sessions) == 2
        assert "[Bob]: long job" in room.claude.prompts[-1]
        assert room.chat == ["welcome back"]
