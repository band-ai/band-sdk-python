"""Unit coverage for RoomTurnEmitter's canonical tool-event wrapping.

The room-visible content of a tool_call/tool_result event is the serialized
``ToolCallRoomEvent`` / ``ToolResultRoomEvent`` wrapper — the seam the e2e
copilot_acp smoke asserts on. These tests pin that contract outside the
nightly-only ``backends`` lane.
"""

from __future__ import annotations

import json

import pytest

from band.integrations.acp.room_emitter import RoomTurnEmitter
from band.integrations.acp.types import (
    ACPToolCall,
    ACPToolResult,
    ChunkType,
    CollectedChunk,
    ToolCallRoomEvent,
    ToolResultRoomEvent,
    ToolStatus,
)
from band.runtime.tools import EVENT_TOOL_NAMES
from band.testing.fake_tools import FakeAgentTools

TOOL_NAME = next(iter(EVENT_TOOL_NAMES))


def make_emitter(tools: FakeAgentTools) -> RoomTurnEmitter:
    return RoomTurnEmitter(tools, mentions=[], session_id="s1", room_id="room-1")


class TestRoomTurnEmitter:
    @pytest.mark.asyncio
    async def test_tool_result_event_wraps_output_exactly_once(self) -> None:
        """The emitted tool_result content is the canonical wrapper; its
        ``output`` field round-trips to the tool's exact response payload."""
        payload = {"id": "abc-123", "message_type": "event", "success": True}
        call = ACPToolCall(tool_call_id="tc-1", name=TOOL_NAME, arguments={})
        result = ACPToolResult(
            call=call, output=json.dumps(payload), status=ToolStatus.COMPLETED
        )
        tools = FakeAgentTools()

        await make_emitter(tools).emit(
            CollectedChunk(
                chunk_type=ChunkType.TOOL_RESULT, content=result.output, tool=result
            )
        )

        assert len(tools.events_sent) == 1
        event = tools.events_sent[0]
        assert event["message_type"] == ChunkType.TOOL_RESULT
        wrapped = ToolResultRoomEvent.model_validate_json(event["content"])
        assert wrapped.name == TOOL_NAME
        assert wrapped.tool_call_id == "tc-1"
        assert wrapped.is_error is False
        assert json.loads(wrapped.output) == payload

    @pytest.mark.asyncio
    async def test_tool_call_event_wraps_args(self) -> None:
        call = ACPToolCall(
            tool_call_id="tc-2",
            name=TOOL_NAME,
            arguments={"message_type": "thought", "content": "hi"},
        )
        tools = FakeAgentTools()

        await make_emitter(tools).emit(
            CollectedChunk(chunk_type=ChunkType.TOOL_CALL, content=call.name, tool=call)
        )

        assert len(tools.events_sent) == 1
        event = tools.events_sent[0]
        assert event["message_type"] == ChunkType.TOOL_CALL
        wrapped = ToolCallRoomEvent.model_validate_json(event["content"])
        assert wrapped.name == TOOL_NAME
        assert wrapped.tool_call_id == "tc-2"
        assert wrapped.args == {"message_type": "thought", "content": "hi"}
