"""Native background callbacks keep their originating execution across queries."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.claude_sdk.activity import CLAUDE_TOOL_USE_META
from band.integrations.mcp.request import request_context
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool
from tests.adapters.claude_sdk.activity.kit import native_tool_result, native_tool_uses
from tests.adapters.claude_sdk.fakecli import Raw
from tests.adapters.claude_sdk.helpers import OpenRoom
from tests.adapters.hold import Hold
from tests.baseline.decisions import ToolCall


class TestClaudeBackgroundOrigins:
    async def test_old_mcp_and_selective_stop_preserve_the_new_foreground(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(emit=())
        recorder = ActivityRecorder()
        original = recorder.observing(room.fresh_tools())
        room.claude.script(
            [
                room.model_reply("Started."),
                native_tool_uses(("parent", "Bash")),
                Raw(
                    {
                        "type": "system",
                        "subtype": "task_started",
                        "task_id": "background",
                        "tool_use_id": "parent",
                        "description": "private task description",
                        "uuid": "started",
                        "session_id": "session-1",
                    }
                ),
                native_tool_result("parent"),
            ]
        )
        await room.send("Start background work", tools=original)
        session = room.session_manager.session(room.room_id)
        assert session is not None and session.activity is not None
        origin = session.activity.resolve("parent")
        assert origin.tools is original
        assert not origin.owner.task.done()
        assert recorder.events[-1] == shown_tool("Bash")

        current = room.fresh_tools()
        hold = Hold()
        room.claude.script([hold, room.model_reply("Foreground complete.")])
        foreground = asyncio.create_task(
            room.send("Continue foreground", tools=current)
        )
        try:
            await hold.arrived()
            peer = room.claude.sessions[-1]
            await peer.background_tool(
                "background",
                "background-reply",
                ToolCall(
                    "mcp__band__band_send_message",
                    {"content": "Background reply.", "mentions": ["@bob"]},
                ),
            )
            assert original.turn.replied
            assert not current.turn.replied
            origin.owner.terminate()
            assert await room.claude.stop_requests.get() == "background"
            assert not origin.owner.task.done()
            assert not foreground.done()
            peer.native_event(
                type="system",
                subtype="task_updated",
                task_id="background",
                patch={"status": "killed"},
            )
            await origin.owner.task
            with (
                request_context({CLAUDE_TOOL_USE_META: "parent"}),
                pytest.raises(ValueError, match="Unknown Claude tool origin"),
            ):
                room.adapter._resolve_mcp_tools(room.room_id)
            assert not foreground.done()
        finally:
            hold.released.set()
            await foreground
        assert current.turn.replied
        assert recorder.events[-1] == SHOWN_REASONING

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
        assert recorder.events[-2:] == [{"kind": "memory"}, SHOWN_REASONING]
