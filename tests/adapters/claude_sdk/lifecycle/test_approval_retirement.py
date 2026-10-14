"""Approval answers and callbacks stay with the residency that asked them."""

from __future__ import annotations

from claude_agent_sdk.types import PermissionResultDeny, ToolPermissionContext

from band.adapters.claude_sdk import RETIRED_SESSION_DENIAL
from tests.adapters.claude_sdk.helpers import WRITE_NOTE, OpenRoom, with_approvals


class TestApprovalRetirement:
    async def test_a_claimed_answer_held_during_leave_cannot_finish_the_rejoined_turn(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(
            with_approvals().model_copy(update={"turn_settle_timeout_s": 0})
        )
        room.claude.script([WRITE_NOTE, room.model_reply("old turn finished")])
        await room.send("write the old note")
        await room.until_said("Approval requested")
        resolving = room.tools.hold_message("resolved as")
        await room.send("/approve a-1")

        async with resolving:
            await room.leave()
            room.claude.script([WRITE_NOTE, room.model_reply("new turn finished")])
            await room.send("write the new note")
            await room.until_said("Approval requested", times=2)
            await room.send("/approve a-1")

        assert room.turn_running
        assert "old turn finished" not in room.chat
        assert room.tool_outputs == {}
        await room.send("/approve a-2")
        await room.settled()

        assert room.chat[-1] == "new turn finished"
        assert room.tool_outputs["Write"] == "Write ran"
        assert [cli.alive for cli in room.claude.sessions] == [False, True]

    async def test_old_callbacks_and_answers_leave_other_rooms_decisions_answerable(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(with_approvals())
        other = room.beside("other-room")
        room.claude.script(
            [WRITE_NOTE, room.model_reply("old note declined")],
            [WRITE_NOTE, other.model_reply("other note written")],
            [WRITE_NOTE, room.model_reply("new note written")],
        )
        await room.send("old note")
        await room.until_said("Approval requested")
        old_cli = room.claude.sessions[0]
        old_session = room.session_manager.session(room.room_id)
        assert old_session is not None
        old_generation = old_session.retained.generation
        await other.send("other note")
        await other.until_said("Approval requested")

        await room.leave()
        await room.send("new note")
        await room.until_said("Approval requested", times=2)
        new_session = room.session_manager.session(room.room_id)
        assert new_session is not None
        assert new_session.retained.generation == old_generation
        assert old_cli.options.can_use_tool is not None
        stale = await old_cli.options.can_use_tool(
            "Write",
            {"file_path": "stale.md"},
            ToolPermissionContext(tool_use_id="late"),
        )
        await room.send("/approve a-1")

        assert stale == PermissionResultDeny(message=RETIRED_SESSION_DENIAL)
        assert room.turn_running and other.turn_running
        await other.send("/approve a-1")
        await other.settled()
        await room.send("/approve a-2")
        await room.settled()

        assert other.chat[-1] == "other note written"
        assert room.chat[-1] == "new note written"
        assert other.tool_outputs["Write"] == "Write ran"
        assert room.tool_outputs["Write"] == "Write ran"
        assert room.failures == other.failures == []
