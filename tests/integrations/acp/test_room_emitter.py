"""Unit coverage for RoomTurnEmitter's canonical tool-event wrapping.

The room-visible content of a tool_call/tool_result event is the serialized
``ToolCallRoomEvent`` / ``ToolResultRoomEvent`` wrapper — the seam the e2e
copilot_acp smoke asserts on. These tests pin that contract outside the
nightly-only ``backends`` lane.
"""

from __future__ import annotations

import json
from typing import ClassVar

import pytest

from band.core.types import Emit
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
    """THOUGHT/PLAN chunks pass raw ``chunk.content`` through unguarded
    (unlike TEXT, which checks truthiness first), so a status-only ACP update
    can reach the send path as a whitespace-only chunk. Exercised against the
    real ``AgentTools`` so that path really hits
    ``band.platform.posting.post_event``'s blank-content refusal.
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


class TestRoomTurnEmitterEmitGating:
    """The constructor's emit set controls which chunk kinds reach the room.

    The adapter hands the emitter the caller's resolved ``features.emit``;
    ``None`` here is the historical all-kinds default. Chunk *recording* is
    unconditional, so the tool-first delivery decision and the text relay
    behave identically whether narration is on or off.
    """

    MENTIONS: ClassVar[list[dict[str, str]]] = [{"id": "u1", "name": "User"}]

    def _chunks(self) -> list[CollectedChunk]:
        call = ACPToolCall(tool_call_id="tc-1", name=TOOL_NAME, arguments={})
        result = ACPToolResult(call=call, output="ok", status=ToolStatus.COMPLETED)
        return [
            CollectedChunk(chunk_type=ChunkType.THOUGHT, content="hmm"),
            CollectedChunk(
                chunk_type=ChunkType.TOOL_CALL, content=call.name, tool=call
            ),
            CollectedChunk(chunk_type=ChunkType.TOOL_RESULT, content="ok", tool=result),
            CollectedChunk(chunk_type=ChunkType.PLAN, content="plan"),
            CollectedChunk(chunk_type=ChunkType.TEXT, content="done"),
        ]

    async def run_turn(
        self, tools: FakeAgentTools, emit: frozenset[Emit] | None
    ) -> None:
        emitter = RoomTurnEmitter(
            tools,
            mentions=self.MENTIONS,
            session_id="s1",
            room_id="room-1",
            emit=emit,
        )
        async with emitter:
            for chunk in self._chunks():
                await emitter.emit(chunk)

    @pytest.mark.asyncio
    async def test_the_default_posts_every_kind(self) -> None:
        tools = FakeAgentTools()

        await self.run_turn(tools, None)

        # thought, tool_call, tool_result, the plan, and the closing
        # session bookkeeping event.
        kinds = [event["message_type"] for event in tools.events_sent]
        assert sorted(kinds) == sorted(
            ["thought", "tool_call", "tool_result", "task", "task"]
        )
        assert [message["content"] for message in tools.messages_sent] == ["done"]

    @pytest.mark.asyncio
    async def test_empty_emit_silences_narration_but_still_replies(self) -> None:
        tools = FakeAgentTools()

        await self.run_turn(tools, frozenset())

        assert tools.events_sent == []
        assert [message["content"] for message in tools.messages_sent] == ["done"]

    @pytest.mark.asyncio
    async def test_a_narrowed_emit_posts_only_the_requested_kinds(self) -> None:
        tools = FakeAgentTools()

        await self.run_turn(tools, frozenset({Emit.TOOL_CALLS}))

        kinds = [event["message_type"] for event in tools.events_sent]
        assert sorted(kinds) == ["tool_call", "tool_result"]
        assert [message["content"] for message in tools.messages_sent] == ["done"]

    @pytest.mark.asyncio
    async def test_silenced_turn_still_suppresses_duplicated_text(self) -> None:
        """A turn that answered in the room via a Band messaging tool must
        not also relay its held text — even with ``emit=()`` hiding that
        tool call from the room."""
        call = ACPToolCall(
            tool_call_id="tc-9",
            name="band_send_message",
            arguments={"content": "hi"},
        )
        result = ACPToolResult(call=call, output="sent", status=ToolStatus.COMPLETED)
        tools = FakeAgentTools()
        emitter = RoomTurnEmitter(
            tools,
            mentions=self.MENTIONS,
            session_id="s1",
            room_id="room-1",
            emit=frozenset(),
        )

        async with emitter:
            await emitter.emit(CollectedChunk(chunk_type=ChunkType.TEXT, content="hi"))
            await emitter.emit(
                CollectedChunk(
                    chunk_type=ChunkType.TOOL_CALL, content=call.name, tool=call
                )
            )
            await emitter.emit(
                CollectedChunk(
                    chunk_type=ChunkType.TOOL_RESULT,
                    content="sent",
                    tool=result,
                    metadata={"status": ToolStatus.COMPLETED},
                )
            )

        assert tools.events_sent == []
        assert tools.messages_sent == []

    @pytest.mark.asyncio
    async def test_a_denied_permission_pair_follows_the_tool_call_gate(self) -> None:
        call = ACPToolCall(tool_call_id="tc-p", name=TOOL_NAME, arguments={})

        quiet = FakeAgentTools()
        await RoomTurnEmitter(
            quiet,
            mentions=[],
            session_id="s1",
            room_id="room-1",
            emit=frozenset(),
        ).open_permission(call=call, session_id="s1", outcome="denied")
        assert quiet.events_sent == []

        loud = FakeAgentTools()
        await RoomTurnEmitter(
            loud,
            mentions=[],
            session_id="s1",
            room_id="room-1",
            emit=frozenset({Emit.TOOL_CALLS}),
        ).open_permission(call=call, session_id="s1", outcome="denied")
        assert [event["message_type"] for event in loud.events_sent] == [
            "tool_call",
            "tool_result",
        ]
