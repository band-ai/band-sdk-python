from __future__ import annotations

from band.adapters.claude_sdk import APPROVAL_RESOLVED_TEMPLATE
from band.testing.activity import ActivityRecorder
from tests.adapters.claude_sdk.helpers import OpenRoom, with_approvals
from tests.baseline.decisions import ModelDecision, ToolCall

BASH_AND_WRITE = ModelDecision(
    tool_calls=(
        ToolCall("Bash", {"command": "ls"}),
        ToolCall("Write", {"file_path": "notes.md", "content": "todo"}),
    )
)


class TestClaudePermissionActivity:
    async def test_the_room_shows_permission_until_its_last_approval_is_answered(
        self, claude_room: OpenRoom, recorder: ActivityRecorder
    ) -> None:
        room = await claude_room(with_approvals())
        recorder.observing(room.tools)
        room.claude.script([BASH_AND_WRITE, room.model_reply("Done.")])

        await room.send("List the files and jot a note")
        await room.until_said("Approval requested", times=2)
        assert recorder.waiting

        await room.send("/approve a-1")
        await room.until_said(
            APPROVAL_RESOLVED_TEMPLATE.format(token="a-1", decision="accept")
        )
        assert recorder.waiting

        await room.send("/approve a-2")
        await room.settled()
        assert not recorder.waiting
