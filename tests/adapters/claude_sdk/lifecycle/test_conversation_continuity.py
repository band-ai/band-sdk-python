"""A replacement CLI continues the retired one's private conversation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from tests.adapters.claude_sdk.fakecli import Recall, Remember
from tests.adapters.claude_sdk.helpers import (
    ClaudeRoom,
    band_server_replaced,
    turn_cancelled,
)

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


class TestConversationContinuity:
    @pytest.mark.parametrize("retire", [band_server_replaced, turn_cancelled])
    async def test_a_replacement_cli_recalls_what_the_retired_one_kept_private(
        self,
        claude_room: OpenRoom,
        retire: Callable[[ClaudeRoom], Awaitable[None]],
    ) -> None:
        """The note never reached the room, so only a resumed conversation can
        answer with it, in the workspace the retired CLI used."""
        room = await claude_room()
        room.claude.script(
            [Remember("the code word is tulip"), room.model_reply("noted")]
        )
        await room.send("remember the code word")

        await retire(room)
        room.claude.script([Recall(room.model_reply)])
        await room.send("what was the code word?")

        first, *_, last = room.claude.sessions
        assert room.chat == ["noted", "the code word is tulip"]
        assert (first.alive, last.alive) == (False, True)
        assert last.options.resume == first.session_id
        assert last.options.cwd == first.options.cwd
        assert room.failures == []
