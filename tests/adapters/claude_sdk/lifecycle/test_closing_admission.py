"""A message that reaches a room while it is leaving is not accepted."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.core.exceptions import InputNotAccepted
from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


class TestClosingAdmission:
    async def test_a_message_during_a_held_leave_is_refused_without_a_turn(
        self, claude_room: OpenRoom
    ) -> None:
        """The host keeps the message for later; nothing runs on, or reports
        from, the session that is closing."""
        room = await claude_room()
        room.claude.script([room.model_reply("hi")])
        await room.send("hi")
        closing = room.claude.closing = Hold()
        leaving = asyncio.create_task(room.leave())

        async with closing:
            with pytest.raises(InputNotAccepted, match="closing"):
                await room.send("are you there?")
        await leaving

        assert len(room.claude.sessions) == 1
        assert room.chat == ["hi"]
        assert room.failures == []
