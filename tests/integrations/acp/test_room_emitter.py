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
from band.runtime.tools.agent import AgentTools
from band.testing.fake_tools import FakeAgentTools

# Arbitrary example name: these tests exercise the generic wrapping mechanism,
# not any one specific platform tool's identity.
TOOL_NAME = "band_send_event"


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


class TestRoomTurnEmitterBlankChunks:
    """THOUGHT/PLAN chunks pass raw ``chunk.content`` unguarded (unlike TEXT,
    which checks truthiness) -- a status-only ACP update can collect as a
    whitespace-only chunk. Exercised against the real ``AgentTools`` (not
    ``FakeAgentTools``, which never rejects anything) so the send path really
    reaches ``band.platform.posting.post_event``'s blank-content refusal:
    before that refusal existed, this content would 422 the REST call and the
    exception would abort the whole ACP turn (the SDK's routing side of
    INT-1361 -- Python already turns that into a stopped runtime with a
    visible error event rather than a silent zombie, but a single blank
    status update should not cost the rest of the turn either).
    """

    @pytest.mark.asyncio
    async def test_a_blank_thought_chunk_does_not_raise(self, mock_rest_client) -> None:
        tools = AgentTools("room-1", mock_rest_client)
        emitter = RoomTurnEmitter(tools, mentions=[], session_id="s1", room_id="room-1")

        await emitter.emit(
            CollectedChunk(chunk_type=ChunkType.THOUGHT, content="   ", tool=None)
        )

        mock_rest_client.agent_api_events.create_agent_chat_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_turn_keeps_emitting_after_a_blank_chunk(
        self, mock_rest_client
    ) -> None:
        tools = AgentTools("room-1", mock_rest_client)
        emitter = RoomTurnEmitter(tools, mentions=[], session_id="s1", room_id="room-1")

        await emitter.emit(
            CollectedChunk(chunk_type=ChunkType.THOUGHT, content="   ", tool=None)
        )
        await emitter.emit(
            CollectedChunk(chunk_type=ChunkType.THOUGHT, content="thinking", tool=None)
        )

        mock_rest_client.agent_api_events.create_agent_chat_event.assert_called_once()
        call_args = mock_rest_client.agent_api_events.create_agent_chat_event.call_args
        assert call_args.kwargs["event"].content == "thinking"
