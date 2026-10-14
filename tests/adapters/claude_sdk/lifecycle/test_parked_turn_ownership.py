"""A turn parked on a person keeps its room owned until the room leaves."""

from __future__ import annotations

import asyncio

from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import (
    WRITE_NOTE,
    WRITE_PROMPT,
    OpenRoom,
    with_approvals,
)


class TestParkedTurnOwnership:
    async def test_a_parked_approval_keeps_its_turn_owned_until_the_room_leaves(
        self, claude_room: OpenRoom
    ) -> None:
        """``on_message`` returns while the turn waits on a person; the detached
        turn still holds the room, and leaving lets it settle, its approval
        declined, before the CLI closes."""
        room = await claude_room(with_approvals())
        room.claude.script([WRITE_NOTE, room.model_reply("Skipped the note.")])

        await room.send("jot a note")
        await room.until_said("Approval requested")
        parked = room.turn_running
        closing = room.claude.closing = Hold()
        leaving = asyncio.create_task(room.leave())
        async with closing:
            running_at_close = room.turn_running
        await leaving

        assert (parked, running_at_close) == (True, False)
        assert [cli.alive for cli in room.claude.sessions] == [False]
        assert room.chat == [WRITE_PROMPT, "Skipped the note."]
        # The turn read its own result, so it finished rather than being cancelled.
        assert room.persisted_sessions == [
            cli.session_id for cli in room.claude.sessions
        ]
        assert room.failures == []
