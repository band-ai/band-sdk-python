from __future__ import annotations

import pytest

from band.adapters.claude_sdk import (
    APPROVAL_RESOLVED_TEMPLATE,
    APPROVAL_TIMED_OUT_TEMPLATE,
)
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

    async def test_permission_lasts_through_claimed_answer_delivery(
        self, claude_room: OpenRoom, recorder: ActivityRecorder
    ) -> None:
        room = await claude_room(with_approvals())
        recorder.observing(room.tools)
        room.claude.script([BASH_AND_WRITE, room.model_reply("Done.")])
        await room.send("List files and write a note")
        await room.until_said("Approval requested", times=2)

        first_answer = room.tools.hold_message(
            APPROVAL_RESOLVED_TEMPLATE.format(token="a-1", decision="accept")
        )
        await room.send("/approve a-1")
        async with first_answer:
            await room.send("/approve a-2")
            await room.until_said(
                APPROVAL_RESOLVED_TEMPLATE.format(token="a-2", decision="accept")
            )
            assert recorder.waiting

        await room.settled()
        assert not recorder.waiting

    @pytest.mark.looptime
    async def test_permission_lasts_through_timeout_answer_delivery(
        self, claude_room: OpenRoom, recorder: ActivityRecorder
    ) -> None:
        room = await claude_room(
            with_approvals(wait_timeout_s=60, timeout_decision="accept")
        )
        recorder.observing(room.tools)
        room.claude.script(
            [ModelDecision.call("Bash", command="ls"), room.model_reply("Done.")]
        )
        answer = room.tools.hold_message(
            APPROVAL_TIMED_OUT_TEMPLATE.format(token="a-1", decision="accept")
        )
        await room.send("List files")
        async with answer:
            assert recorder.waiting
        await room.settled()
        assert not recorder.waiting

    async def test_cancelling_claimed_answer_delivery_clears_permission(
        self, claude_room: OpenRoom, recorder: ActivityRecorder
    ) -> None:
        config = with_approvals().model_copy(update={"turn_settle_timeout_s": 0})
        room = await claude_room(config)
        recorder.observing(room.tools)
        room.claude.script(
            [ModelDecision.call("Bash", command="ls"), room.model_reply("Done.")]
        )
        await room.send("List files")
        await room.until_said("Approval requested")
        answer = room.tools.hold_message(
            APPROVAL_RESOLVED_TEMPLATE.format(token="a-1", decision="accept")
        )
        await room.send("/approve a-1")
        async with answer:
            assert recorder.waiting
            await room.adapter.cleanup_all()
            assert not recorder.waiting
