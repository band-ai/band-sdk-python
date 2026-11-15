"""Claude compaction ends only on its matching native status transition."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.activity import MEMORY
from band.runtime.tools.types import BandTool
from band.testing.activity import (
    SHOWN_MEMORY,
    SHOWN_REASONING,
    ActivityRecorder,
    shown_tool,
)
from tests.adapters.claude_sdk.activity.kit import native_tool_result, native_tool_uses
from tests.adapters.claude_sdk.fakecli import Raw
from tests.adapters.claude_sdk.helpers import OpenRoom
from tests.adapters.hold import Hold

READER_BARRIER = "reader-barrier"
BARRIER_TOOL = "Read"


class TestClaudeCompaction:
    @pytest.mark.parametrize(
        "completion",
        [[Raw({"type": "system", "subtype": "status", "status": None})], []],
        ids=["matching-status", "foreground-result"],
    )
    async def test_compaction_ignores_permission_mode_status(
        self, claude_room: OpenRoom, completion: list[Raw]
    ) -> None:
        room = await claude_room(emit=())
        recorder = ActivityRecorder()
        recorder.observing(room.tools)
        hold = Hold()
        room.claude.script(
            [
                room.model_reply("Ready."),
                native_tool_uses((READER_BARRIER, BARRIER_TOOL)),
                Raw({"type": "system", "subtype": "status", "status": "compacting"}),
                Raw(
                    {
                        "type": "system",
                        "subtype": "status",
                        "status": None,
                        "permissionMode": "default",
                    }
                ),
                native_tool_result(READER_BARRIER),
                hold,
                *completion,
            ]
        )
        sending = asyncio.create_task(room.send("Compact context"))
        try:
            await hold.arrived()
            await recorder.next_end()
            # The marker's end follows the permission status in the actual reader.
            await recorder.next_end()
            assert recorder.activity.detail == MEMORY
        finally:
            hold.released.set()
            await sending
        assert recorder.events == [
            shown_tool(BandTool.SEND_MESSAGE),
            SHOWN_REASONING,
            shown_tool(BARRIER_TOOL),
            SHOWN_MEMORY,
            SHOWN_REASONING,
        ]
