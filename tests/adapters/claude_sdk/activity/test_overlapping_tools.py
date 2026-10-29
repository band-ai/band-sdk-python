"""A native tool that finishes never hides another native tool still running."""

from __future__ import annotations

import pytest

from band.core.types import Emit
from band.runtime.tools.types import BandTool
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool
from tests.adapters.claude_sdk.activity.kit import (
    native_tool_result,
    native_tool_uses,
)
from tests.adapters.claude_sdk.helpers import OpenRoom


class TestClaudeOverlappingToolActivity:
    @pytest.mark.parametrize(
        "emit", [(Emit.TOOL_CALLS,), ()], ids=["narration-on", "narration-off"]
    )
    async def test_the_older_call_shows_again_when_the_newer_finishes_first(
        self,
        claude_room: OpenRoom,
        recorder: ActivityRecorder,
        emit: tuple[Emit, ...],
    ) -> None:
        room = await claude_room(emit=emit)
        recorder.observing(room.tools)
        room.claude.script(
            [
                room.model_reply("Reading and listing."),
                native_tool_uses(("toolu_read", "Read"), ("toolu_bash", "Bash")),
                native_tool_result("toolu_bash"),
                native_tool_result("toolu_read"),
            ]
        )

        await room.send("Read the notes and list the files")

        assert recorder.events == [
            shown_tool(BandTool.SEND_MESSAGE),
            SHOWN_REASONING,
            shown_tool("Read"),
            shown_tool("Bash"),
            shown_tool("Read"),
            SHOWN_REASONING,
        ]
