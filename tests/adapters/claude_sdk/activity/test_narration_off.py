from __future__ import annotations

from band.core.types import MessageType
from band.testing.activity import ActivityRecorder
from tests.adapters.claude_sdk.activity.kit import (
    REPLY_THEN_BASH_ACTIVITY,
    reply_then_bash,
)
from tests.adapters.claude_sdk.helpers import OpenRoom

NARRATION = {MessageType.TOOL_CALL, MessageType.TOOL_RESULT}


class TestActivityWithoutNarration:
    async def test_a_native_tool_reports_activity_while_narration_is_off(
        self, claude_room: OpenRoom, recorder: ActivityRecorder
    ) -> None:
        room = await claude_room(emit=())
        recorder.observing(room.tools)
        room.claude.script(reply_then_bash(room))

        await room.send("List the files")

        assert recorder.events == REPLY_THEN_BASH_ACTIVITY
        assert NARRATION.isdisjoint(room.events)
