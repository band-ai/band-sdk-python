from __future__ import annotations

from band.runtime.working_state import report_tool_activity
from band.testing.activity import ActivityRecorder
from tests.adapters.claude_sdk.helpers import OpenRoom, with_approvals
from tests.adapters.opencode.helpers import wait_for
from tests.baseline.decisions import ModelDecision, ToolCall


class TestClaudePermissionActivity:
    async def test_overlapping_native_waits_include_claimed_notice_delivery(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(with_approvals(), emit=())
        recorder = ActivityRecorder()
        room.tools._activity_observer = recorder
        room.claude.script(
            [
                ModelDecision(
                    tool_calls=(
                        ToolCall("Bash", {"command": "private-command"}),
                        ToolCall(
                            "Write",
                            {"file_path": "private-path", "content": "private-content"},
                        ),
                    )
                ),
                room.model_reply("Done"),
            ]
        )
        held = room.tools.hold_message("resolved")
        await room.send("Do both")
        await room.until_said("Approval requested", times=2)
        await room.send("/approve a-1")
        async with held:
            assert recorder.waiting
            await report_tool_activity(room.tools, "ordinary-tool")
            await room.send("/approve a-2")
            await wait_for(
                lambda: len(room.adapter._pending_approvals[room.room_id]) == 1
            )
            assert recorder.waiting
        await room.settled()
        assert not recorder.waiting
        assert not room.tool_call_names
