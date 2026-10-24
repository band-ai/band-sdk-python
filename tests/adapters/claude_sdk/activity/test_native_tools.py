from __future__ import annotations

from band.testing.activity import ActivityRecorder
from tests.adapters.claude_sdk.activity.kit import (
    REPLY_THEN_BASH_ACTIVITY,
    reply_then_bash,
)
from tests.adapters.claude_sdk.helpers import OpenRoom


class TestClaudeNativeToolActivity:
    async def test_a_native_tool_shows_until_its_result_and_band_tools_show_once(
        self, claude_room: OpenRoom, recorder: ActivityRecorder
    ) -> None:
        """The reply's MCP call reports itself, so its streamed tool_use does
        not report it again; a native tool reports from the stream."""
        room = await claude_room()
        recorder.observing(room.tools)
        room.claude.script(reply_then_bash(room))

        await room.send("List the files")

        assert recorder.events == REPLY_THEN_BASH_ACTIVITY
