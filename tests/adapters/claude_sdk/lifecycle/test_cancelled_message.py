"""A message the runtime cancels takes its turn and CLI process with it."""

from __future__ import annotations

import asyncio

import pytest

from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import OpenRoom


class TestCancelledMessage:
    async def test_a_cancelled_message_never_leaks_its_turn_into_the_next(
        self, claude_room: OpenRoom
    ) -> None:
        """The runtime cancelling a message cancels its detached turn and retires
        that CLI process, so nothing the abandoned turn would still do reaches the
        room or is read as the next message's answer; the next message resumes the
        conversation in a fresh process."""
        room = await claude_room()
        abandoned = Hold()
        room.claude.script(
            [room.model_reply("first")],
            [abandoned, room.model_reply("abandoned reply")],
            [room.model_reply("second")],
        )

        await room.send("quick")
        message = asyncio.create_task(room.send("slow"))
        async with abandoned:
            message.cancel()
            with pytest.raises(asyncio.CancelledError):
                await message
        await room.send("again")

        assert room.chat == ["first", "second"]
        assert room.failures == []
        assert room.claude.resumed == [None, "sess-1"]
