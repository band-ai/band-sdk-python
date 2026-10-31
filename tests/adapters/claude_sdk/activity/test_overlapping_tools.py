"""A native tool that finishes never hides another native tool still running."""

from __future__ import annotations

import asyncio

import pytest

from band.core.types import Emit
from band.runtime.activity import tool_call_activity
from band.runtime.tools.types import BandTool
from band.testing.activity import SHOWN_REASONING, shown_tool
from tests.adapters.activityrecorder import EndNotifyingRecorder
from tests.adapters.claude_sdk.activity.kit import (
    native_tool_result,
    native_tool_uses,
)
from tests.adapters.claude_sdk.helpers import OpenRoom
from tests.adapters.hold import Hold


class TestClaudeOverlappingToolActivity:
    @pytest.mark.parametrize(
        "emit", [(Emit.TOOL_CALLS,), ()], ids=["narration-on", "narration-off"]
    )
    @pytest.mark.parametrize(
        ("second_name", "first_finished", "remaining", "expected"),
        [
            (
                "Bash",
                "toolu_read",
                "toolu_bash",
                [shown_tool("Read"), shown_tool("Bash"), SHOWN_REASONING],
            ),
            (
                "Bash",
                "toolu_bash",
                "toolu_read",
                [
                    shown_tool("Read"),
                    shown_tool("Bash"),
                    shown_tool("Read"),
                    SHOWN_REASONING,
                ],
            ),
            ("Read", "toolu_read", "toolu_bash", [shown_tool("Read"), SHOWN_REASONING]),
            ("Read", "toolu_bash", "toolu_read", [shown_tool("Read"), SHOWN_REASONING]),
        ],
        ids=[
            "different-older-first",
            "different-newer-first",
            "same-older-first",
            "same-newer-first",
        ],
    )
    async def test_finishing_one_call_keeps_the_other_visible(
        self,
        claude_room: OpenRoom,
        emit: tuple[Emit, ...],
        second_name: str,
        first_finished: str,
        remaining: str,
        expected: list[dict[str, str]],
    ) -> None:
        room = await claude_room(emit=emit)
        recorder = EndNotifyingRecorder(first_finished)
        hold = Hold()
        recorder.observing(room.tools)
        room.claude.script(
            [
                room.model_reply("Reading and listing."),
                native_tool_uses(("toolu_read", "Read"), ("toolu_bash", second_name)),
                native_tool_result(first_finished),
                hold,
                native_tool_result(remaining),
            ]
        )

        sending = asyncio.create_task(room.send("Read the notes and list the files"))
        try:
            await hold.arrived()
            await recorder.ended.wait()
            remaining_name = {"toolu_read": "Read", "toolu_bash": second_name}[
                remaining
            ]
            assert recorder.activity.detail == tool_call_activity(remaining_name)
        finally:
            recorder.release.set()
            hold.released.set()
            await sending

        assert recorder.events == [
            shown_tool(BandTool.SEND_MESSAGE),
            SHOWN_REASONING,
            *expected,
        ]
