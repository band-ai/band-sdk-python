"""A turn released on an approval still answers for its message when its room
closes: cancelled past its settle grace, it is reported once."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.adapters.claude_sdk import (
    APPROVAL_REQUESTED_TEMPLATE,
    ClaudeApprovalOptions,
    ClaudeSDKAdapterConfig,
)
from band.core.turn import MISSING_REPLY
from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import WRITE_NOTE, ClaudeRoom, OpenRoom

WRITE_PROMPT = APPROVAL_REQUESTED_TEMPLATE.format(
    summary="Write: notes.md", token="a-1"
)


async def leave(room: ClaudeRoom) -> None:
    await room.leave()


async def shut_down(room: ClaudeRoom) -> None:
    await room.adapter.cleanup_all()


class TestDetachedTurnOnClose:
    @pytest.mark.parametrize("closing", [leave, shut_down])
    async def test_a_released_turn_cancelled_by_its_room_closing_is_reported_once(
        self, claude_room: OpenRoom, closing: Callable[[ClaudeRoom], Awaitable[None]]
    ) -> None:
        room = await claude_room(
            ClaudeSDKAdapterConfig(
                approvals=ClaudeApprovalOptions(mode="manual"),
                turn_settle_timeout_s=0,
            )
        )
        room.claude.script([WRITE_NOTE, Hold()])
        await room.send("jot a note")
        await room.until_said("Approval requested")

        await closing(room)

        assert room.chat == [WRITE_PROMPT]
        assert room.failures == [MISSING_REPLY.message]
