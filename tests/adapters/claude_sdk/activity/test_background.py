"""Native background callbacks keep their originating execution across queries."""

from __future__ import annotations

import asyncio

import pytest
from band_rest.types.agent_activity_detail import AgentActivityDetail

from band.core.exceptions import RoomClosingError
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.claude_sdk.activity import CLAUDE_TOOL_USE_META, Origin
from band.integrations.mcp.request import request_context
from band.testing import FakeAgentTools
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool
from tests.adapters.claude_sdk.activity.kit import (
    BACKGROUND_CALL,
    BACKGROUND_TASK,
    native_tool_uses,
)
from tests.adapters.claude_sdk.fakecli import CLEANUP_FAILED
from tests.adapters.claude_sdk.helpers import ClaudeRoom
from tests.adapters.hold import Hold
from tests.baseline.decisions import ToolCall


class TestClaudeBackgroundOrigins:
    async def test_old_mcp_and_selective_stop_preserve_the_new_foreground(
        self,
        background_origin: tuple[ClaudeRoom, FakeAgentTools, ActivityRecorder, Origin],
    ) -> None:
        room, original, recorder, origin = background_origin
        session = room.session_manager.session(room.room_id)
        assert session is not None and session.activity is not None
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
                BACKGROUND_TASK,
                "background-reply",
                ToolCall(
                    "mcp__band__band_send_message",
                    {"content": "Background reply.", "mentions": ["@bob"]},
                ),
            )
            assert original.turn.replied
            assert not current.turn.replied
            origin.owner.terminate()
            assert await room.claude.stop_requests.get() == BACKGROUND_TASK
            assert not origin.owner.task.done()
            assert not foreground.done()
            peer.native_event(
                type="system",
                subtype="task_updated",
                task_id=BACKGROUND_TASK,
                patch={"status": "killed"},
            )
            await origin.owner.task
            with (
                request_context({CLAUDE_TOOL_USE_META: BACKGROUND_CALL}),
                pytest.raises(ValueError, match="Unknown Claude tool origin"),
            ):
                room.adapter._resolve_mcp_tools(room.room_id)
            assert not foreground.done()
        finally:
            hold.released.set()
            await foreground
        assert current.turn.replied
        assert recorder.events[-1] == SHOWN_REASONING

    async def test_failed_selective_stop_revokes_the_shared_generation_before_close(
        self,
        background_origin: tuple[ClaudeRoom, FakeAgentTools, ActivityRecorder, Origin],
    ) -> None:
        room, original, _, origin = background_origin
        session = room.session_manager.session(room.room_id)
        assert session is not None and session.activity is not None
        generation = session.generation
        peer = room.claude.sessions[-1]
        room.claude.refuse_task_stop = True
        closing, foreground_hold = Hold(), Hold()
        room.claude.closing = closing
        recorder = ActivityRecorder()
        current = recorder.observing(room.fresh_tools())
        room.claude.script(
            [
                native_tool_uses(("current-call", "Read")),
                foreground_hold,
                room.model_reply("Must not be delivered."),
            ]
        )
        foreground = asyncio.create_task(room.send("New foreground", tools=current))
        try:
            await foreground_hold.arrived()
            await recorder.wait_for_detail(
                AgentActivityDetail(kind="tool_call", name="Read")
            )
            current_origin = session.activity.resolve("current-call")
            origin.owner.terminate()
            assert await room.claude.stop_requests.get() == BACKGROUND_TASK
            await closing.arrived()
            assert origin.owner.deadline is not None
            assert current_origin.owner.deadline is not None
            assert not origin.owner.task.done()
            assert peer.alive
            with pytest.raises(TurnResultAlreadyReported):
                await foreground
            closing.released.set()
            await session.retained.wait_released(generation=generation)
            await origin.owner.task
            assert not peer.alive
            assert original.turn.replied
            assert not current.turn.replied
            assert "Must not be delivered." not in room.chat
        finally:
            closing.released.set()
            foreground_hold.released.set()
            await asyncio.gather(foreground, return_exceptions=True)

    async def test_failed_session_close_keeps_native_cleanup_owned_for_retry(
        self,
        background_origin: tuple[ClaudeRoom, FakeAgentTools, ActivityRecorder, Origin],
    ) -> None:
        room, original, _, origin = background_origin
        peer = room.claude.sessions[-1]
        room.claude.refuse_task_stop = True
        peer.refuse_close = True
        closing = room.claude.closing = Hold()
        closing.released.set()
        try:
            origin.owner.terminate()
            assert await room.claude.stop_requests.get() == BACKGROUND_TASK
            await closing.arrived()
            with pytest.raises(RuntimeError, match=CLEANUP_FAILED):
                await room.leave()
            assert peer.alive
            assert not origin.owner.task.done()
            with pytest.raises(RoomClosingError):
                await room.send("Unsafe replacement")
            assert len(room.claude.sessions) == 1
            peer.refuse_close = False
            await room.leave()
            await origin.owner.task
            assert not peer.alive
            assert room.disposed
            assert original.turn.replied
        finally:
            peer.refuse_close = False
