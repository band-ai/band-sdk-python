"""Closing a room under a live turn cancels the turn before its session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import (
    NO_SETTLE_GRACE,
    ClaudeRoom,
    OpenRoom,
    left,
    shut_down,
)


class TestTeardownMidTurn:
    @pytest.mark.parametrize("teardown", [left, shut_down])
    async def test_teardown_mid_turn_stops_the_turn_before_closing_its_session(
        self,
        claude_room: OpenRoom,
        teardown: Callable[[ClaudeRoom], Awaitable[None]],
    ) -> None:
        """Closing the session under a live turn would surface as a dead CLI; a
        turn past its settle grace is cancelled first, so the room sees neither a
        reply nor a failure."""
        room = await claude_room(NO_SETTLE_GRACE)
        thinking = Hold()
        room.claude.script([thinking, room.model_reply("never sent")])

        message = asyncio.create_task(room.send("long job"))
        async with thinking:
            await teardown(room)

        with pytest.raises(asyncio.CancelledError):
            await message
        [session] = room.claude.sessions
        assert not session.alive
        assert room.chat == []
        assert room.failures == []
