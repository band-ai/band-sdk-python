"""Claude compaction ends only on its matching native status transition."""

from __future__ import annotations

from band.testing.activity import SHOWN_MEMORY, SHOWN_REASONING, ActivityRecorder
from tests.adapters.claude_sdk.fakecli import Raw
from tests.adapters.claude_sdk.helpers import OpenRoom


class TestClaudeCompaction:
    async def test_compaction_ignores_permission_mode_status(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(emit=())
        recorder = ActivityRecorder()
        recorder.observing(room.tools)
        room.claude.script(
            [
                room.model_reply("Ready."),
                Raw({"type": "system", "subtype": "status", "status": "compacting"}),
                Raw(
                    {
                        "type": "system",
                        "subtype": "status",
                        "status": None,
                        "permissionMode": "default",
                    }
                ),
                Raw({"type": "system", "subtype": "status", "status": None}),
            ]
        )
        await room.send("Compact context")
        assert recorder.events[-2:] == [SHOWN_MEMORY, SHOWN_REASONING]
