"""A turn's approval audience ends with the turn, however the turn ends."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from tests.adapters.claude_sdk.batch.kit import (
    ADMIN,
    LIST_FILES,
    OpenRoom,
    alice_helper_alice,
)
from tests.adapters.claude_sdk.helpers import (
    ClaudeRoom,
    left,
    shut_down,
    with_approvals,
)


class TestAudienceRelease:
    async def test_a_turn_that_finishes_leaves_no_audience(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(with_approvals())
        room.claude.script([LIST_FILES, room.model_reply("Listed.")])
        await room.send_batch(*alice_helper_alice(room))

        await room.send("/approve", sender=ADMIN)
        await room.settled()

        assert room.adapter._turn_audience == {}

    @pytest.mark.parametrize("closing", [left, shut_down])
    async def test_a_room_closed_while_the_turn_awaits_approval_leaves_no_audience(
        self, claude_room: OpenRoom, closing: Callable[[ClaudeRoom], Awaitable[None]]
    ) -> None:
        room = await claude_room(
            with_approvals().model_copy(update={"turn_settle_timeout_s": 0})
        )
        room.claude.script([LIST_FILES, room.model_reply("Never sent.")])
        await room.send_batch(*alice_helper_alice(room))
        await room.until_said("Approval requested")

        await closing(room)

        assert room.adapter._turn_audience == {}
