"""Unit coverage for RoomTurnEmitter's canonical tool-event wrapping.

The room-visible content of a tool_call/tool_result event is the serialized
``ToolCallRoomEvent`` / ``ToolResultRoomEvent`` wrapper — the seam the e2e
copilot_acp smoke asserts on. These tests pin that contract outside the
nightly-only ``backends`` lane.
"""

from __future__ import annotations

import json

import pytest

from band.converters.acp_client import ACPClientHistoryConverter
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
from band.runtime.tools import (
    BAND_MCP_SERVER_NAME,
    BandTool,
    TurnEffect,
    mcp_tool_spelling,
)
from band.runtime.tools.agent import AgentTools
from band.testing.fake_tools import FakeAgentTools, events_of_type

# Arbitrary example name: these tests exercise the generic wrapping mechanism,
# not any one specific platform tool's identity.
TOOL_NAME = "band_send_event"


def make_emitter(tools: FakeAgentTools) -> RoomTurnEmitter:
    return RoomTurnEmitter(tools, session_id="s1", room_id="room-1")


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
        emitter = RoomTurnEmitter(tools, session_id="s1", room_id="room-1")

        await emitter.emit(
            CollectedChunk(chunk_type=ChunkType.THOUGHT, content="   ", tool=None)
        )

        mock_rest_client.agent_api_events.create_agent_chat_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_turn_keeps_emitting_after_a_blank_chunk(
        self, mock_rest_client
    ) -> None:
        tools = AgentTools("room-1", mock_rest_client)
        emitter = RoomTurnEmitter(tools, session_id="s1", room_id="room-1")

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
    """The constructor's emit set controls which narration kinds reach the room.

    The adapter hands the emitter the caller's resolved ``features.emit``;
    ``None`` here is the historical all-kinds default. Chunk *recording* is
    unconditional, so the tool-first delivery decision and the text relay
    behave identically whether narration is on or off. The closing session
    bookkeeping event is resume state, not narration, so it is never gated.
    """

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

        # thought, tool_call, tool_result, the plan, then the closing
        # session bookkeeping event last.
        kinds = [event["message_type"] for event in tools.events_sent]
        assert kinds == [
            "thought",
            "tool_call",
            "tool_result",
            "task",
            "thought",
            "task",
        ]
        assert tools.events_sent[-1]["metadata"] == {
            "acp_client_session_id": "s1",
            "acp_client_room_id": "room-1",
        }
        assert tools.messages_sent == []
        assert not tools.turn.complete

    @pytest.mark.asyncio
    async def test_empty_emit_silences_narration_without_settling(self) -> None:
        tools = FakeAgentTools()

        await self.run_turn(tools, frozenset())

        # Only the ungated session bookkeeping event remains.
        assert [event["content"] for event in tools.events_sent] == [
            "ACP client session"
        ]
        assert tools.messages_sent == []
        assert not tools.turn.complete

    @pytest.mark.asyncio
    async def test_a_narrowed_emit_posts_only_the_requested_kinds(self) -> None:
        tools = FakeAgentTools()

        await self.run_turn(tools, frozenset({Emit.TOOL_CALLS}))

        kinds = [event["message_type"] for event in tools.events_sent]
        assert kinds == ["tool_call", "tool_result", "task"]
        assert tools.events_sent[-1]["content"] == "ACP client session"
        assert tools.messages_sent == []
        assert not tools.turn.complete

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "emit",
        [None, frozenset(), frozenset({Emit.TOOL_CALLS})],
        ids=["default", "silenced", "tool-calls-only"],
    )
    async def test_every_emit_set_keeps_session_resume(
        self, emit: frozenset[Emit] | None
    ) -> None:
        """The posted events must round-trip through the history converter to
        the room→session map, or a restart skips native ``session/load``."""
        tools = FakeAgentTools()

        await self.run_turn(tools, emit)

        state = ACPClientHistoryConverter().convert(tools.events_sent)
        assert state.room_to_session == {"room-1": "s1"}

    @pytest.mark.asyncio
    async def test_silenced_turn_still_suppresses_duplicated_text(self) -> None:
        """A turn that answered in the room via an out-of-process Band
        messaging tool must not also relay its held text — even with
        ``emit=()`` hiding that tool call from the room."""
        call = ACPToolCall(
            tool_call_id="tc-9",
            name="band_send_message",
            arguments={"content": "hi"},
        )
        result = ACPToolResult(call=call, output="sent", status=ToolStatus.COMPLETED)
        tools = FakeAgentTools()
        emitter = RoomTurnEmitter(
            tools,
            session_id="s1",
            room_id="room-1",
            emit=frozenset(),
            records_tool_effects=True,
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

        assert [event["content"] for event in tools.events_sent] == [
            "ACP client session"
        ]
        assert tools.messages_sent == []

    @pytest.mark.asyncio
    async def test_a_denied_permission_pair_follows_the_tool_call_gate(self) -> None:
        call = ACPToolCall(tool_call_id="tc-p", name=TOOL_NAME, arguments={})

        quiet = FakeAgentTools()
        await RoomTurnEmitter(
            quiet,
            session_id="s1",
            room_id="room-1",
            emit=frozenset(),
        ).open_permission(call=call, session_id="s1", outcome="denied")
        assert quiet.events_sent == []

        loud = FakeAgentTools()
        await RoomTurnEmitter(
            loud,
            session_id="s1",
            room_id="room-1",
            emit=frozenset({Emit.TOOL_CALLS}),
        ).open_permission(call=call, session_id="s1", outcome="denied")
        assert [event["message_type"] for event in loud.events_sent] == [
            "tool_call",
            "tool_result",
        ]


def tool_call_chunk(
    name: str, status: ToolStatus, *, tool_call_id: str = "tc-1"
) -> CollectedChunk:
    call = ACPToolCall(tool_call_id=tool_call_id, name=name, arguments={})
    return CollectedChunk(
        chunk_type=ChunkType.TOOL_CALL,
        content=name,
        metadata={"status": status},
        tool=call,
    )


def tool_result_chunk(name: str, status: ToolStatus) -> CollectedChunk:
    """A result the runtime correlated to its in-progress call."""
    call = ACPToolCall(tool_call_id="tc-1", name=name, arguments={})
    return CollectedChunk(
        chunk_type=ChunkType.TOOL_RESULT,
        content="",
        metadata={"status": status},
        tool=ACPToolResult(call=call, output="", status=status),
    )


async def closing_messages(
    tools: FakeAgentTools,
    *chunks: CollectedChunk,
    emit: frozenset[Emit] | None = None,
    records_tool_effects: bool = False,
) -> list[str]:
    """Run one turn of ``chunks`` and return the messages it posted."""
    emitter = RoomTurnEmitter(
        tools,
        session_id="s1",
        room_id="room-1",
        emit=emit,
        records_tool_effects=records_tool_effects,
    )
    async with emitter:
        for chunk in chunks:
            await emitter.emit(chunk)
    return [message["content"] for message in tools.messages_sent]


def text(content: str) -> CollectedChunk:
    return CollectedChunk(chunk_type=ChunkType.TEXT, content=content)


class TestRoomTurnEmitterClosingThought:
    """Held text emits as a thought unless a tool replied or declined.

    In-process Band tools record their own effect on ``tools.turn``; an
    external band-mcp's calls are seen only in the stream, so the emitter
    records them itself, and only in that mode.
    """

    @pytest.mark.asyncio
    async def test_held_runs_emit_as_one_thought(self) -> None:
        tools = FakeAgentTools()

        sent = await closing_messages(
            tools,
            text("Checking."),
            tool_call_chunk("shell", ToolStatus.COMPLETED),
            text("Done."),
            records_tool_effects=False,
        )

        assert sent == []
        assert not tools.turn.complete
        assert [e["content"] for e in events_of_type(tools, "thought")] == [
            "Checking.\n\nDone."
        ]

    @pytest.mark.asyncio
    async def test_an_in_process_reply_suppresses_the_text(self) -> None:
        tools = FakeAgentTools()
        await tools.send_message("Posted by the tool.", mentions=["u1"])

        sent = await closing_messages(
            tools,
            tool_call_chunk(BandTool.SEND_MESSAGE, ToolStatus.COMPLETED),
            text("I posted it."),
            records_tool_effects=False,
        )

        assert sent == ["Posted by the tool."]

    @pytest.mark.asyncio
    async def test_in_process_mode_never_records_from_the_stream(self) -> None:
        """The stream reports a call the in-process tool already recorded (or
        that never posted); only the tool itself may settle the reply."""
        sent = await closing_messages(
            FakeAgentTools(),
            tool_call_chunk(BandTool.SEND_MESSAGE, ToolStatus.COMPLETED),
            text("The answer."),
            records_tool_effects=False,
        )

        assert sent == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "chunks",
        [
            [tool_call_chunk(BandTool.SEND_MESSAGE, ToolStatus.COMPLETED)],
            [
                tool_call_chunk(BandTool.SEND_MESSAGE, ToolStatus.IN_PROGRESS),
                tool_result_chunk(BandTool.SEND_MESSAGE, ToolStatus.COMPLETED),
            ],
            [tool_call_chunk(BandTool.NO_REPLY, ToolStatus.COMPLETED)],
            [
                tool_call_chunk(
                    mcp_tool_spelling(BAND_MCP_SERVER_NAME, BandTool.NO_REPLY),
                    ToolStatus.COMPLETED,
                )
            ],
        ],
        ids=["completed-call", "completed-result", "no-reply", "mcp-spelled-no-reply"],
    )
    async def test_an_out_of_process_reply_or_decline_suppresses_the_text(
        self, chunks: list[CollectedChunk]
    ) -> None:
        tools = FakeAgentTools()

        sent = await closing_messages(
            tools, *chunks, text("Narration."), records_tool_effects=True
        )

        assert sent == []
        assert tools.turn.replied

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "chunk",
        [
            tool_call_chunk(BandTool.NO_REPLY, ToolStatus.FAILED),
            tool_call_chunk(BandTool.SEND_MESSAGE, ToolStatus.IN_PROGRESS),
            tool_call_chunk("get_weather", ToolStatus.COMPLETED),
            tool_call_chunk(
                mcp_tool_spelling("other", BandTool.SEND_MESSAGE), ToolStatus.COMPLETED
            ),
        ],
        ids=["failed-no-reply", "unfinished-send", "non-band-tool", "foreign-server"],
    )
    async def test_an_out_of_process_call_that_did_not_reply_keeps_the_text(
        self, chunk: CollectedChunk
    ) -> None:
        sent = await closing_messages(
            FakeAgentTools(), chunk, text("The answer."), records_tool_effects=True
        )

        assert sent == []

    @pytest.mark.asyncio
    async def test_a_failed_prompt_records_nothing_streamed_during_it(self) -> None:
        """A rejected prompt (e.g. busy) owns no work: a reply streamed during
        it belongs to another turn and must not suppress the retry's answer."""
        tools = FakeAgentTools()
        emitter = RoomTurnEmitter(
            tools,
            session_id="s1",
            room_id="room-1",
            records_tool_effects=True,
        )

        with pytest.raises(RuntimeError):
            async with emitter:
                await emitter.emit(
                    tool_call_chunk(BandTool.SEND_MESSAGE, ToolStatus.COMPLETED)
                )
                raise RuntimeError("session busy")

        assert not tools.turn.replied

    @pytest.mark.asyncio
    async def test_an_out_of_process_action_completes_the_turn(self) -> None:
        tools = FakeAgentTools()

        await closing_messages(
            tools,
            tool_call_chunk(BandTool.CREATE_TASK, ToolStatus.COMPLETED),
            records_tool_effects=True,
        )

        assert tools.turn.complete


class TestRoomTurnEmitterAssistantTextAsThought:
    """Closing native text is telemetry and cannot complete an empty turn."""

    @pytest.mark.asyncio
    async def test_text_only_turn_posts_a_thought_and_no_reply(self) -> None:
        tools = FakeAgentTools()

        await closing_messages(
            tools, text("(Waiting on the review."), text("Nothing to change.)")
        )

        assert tools.messages_sent == []
        thoughts = events_of_type(tools, "thought")
        assert [e["content"] for e in thoughts] == [
            "(Waiting on the review.\n\nNothing to change.)"
        ]
        assert not tools.turn.complete
        # Resume state is persisted independently of the verdict.
        assert tools.events_sent[-1]["content"] == "ACP client session"

    @pytest.mark.asyncio
    async def test_thoughts_outside_the_emit_set_leave_the_room_silent(self) -> None:
        tools = FakeAgentTools()

        await closing_messages(tools, text("Nothing to change."), emit=frozenset())

        assert tools.messages_sent == []
        assert [e["content"] for e in tools.events_sent] == ["ACP client session"]
        assert not tools.turn.complete

    @pytest.mark.asyncio
    async def test_a_turn_without_text_still_owes_a_reply(self) -> None:
        tools = FakeAgentTools()

        await closing_messages(tools, text("   "))

        assert tools.messages_sent == []
        assert not tools.turn.complete

    @pytest.mark.asyncio
    async def test_text_after_a_reply_is_not_repeated_as_a_thought(self) -> None:
        tools = FakeAgentTools()
        await tools.send_message("Posted by the tool.", mentions=["u1"])

        await closing_messages(
            tools,
            tool_call_chunk(BandTool.SEND_MESSAGE, ToolStatus.COMPLETED),
            text("I posted it."),
            emit=frozenset({Emit.THOUGHTS}),
        )

        assert [m["content"] for m in tools.messages_sent] == ["Posted by the tool."]
        assert [e["message_type"] for e in tools.events_sent] == ["task"]

    @pytest.mark.asyncio
    async def test_a_failed_in_process_reply_still_owes_the_reply(self) -> None:
        """A reply that did not land is not the model choosing silence: the
        turn must stay unsettled so the runtime reports the missing reply."""
        tools = FakeAgentTools()
        tools.send_message_error = RuntimeError("messages API unavailable")
        with pytest.raises(RuntimeError):
            await tools.send_message("The answer.", mentions=["u1"])

        await closing_messages(tools, text("I posted the answer."))

        assert tools.messages_sent == []
        assert not tools.turn.complete

    @pytest.mark.asyncio
    async def test_a_failed_out_of_process_reply_still_owes_the_reply(self) -> None:
        tools = FakeAgentTools()

        await closing_messages(
            tools,
            tool_result_chunk(BandTool.SEND_MESSAGE, ToolStatus.FAILED),
            text("I posted the answer."),
            records_tool_effects=True,
        )

        assert tools.messages_sent == []
        assert not tools.turn.complete

    @pytest.mark.asyncio
    async def test_an_injected_reply_the_tool_refused_still_owes_the_reply(
        self,
    ) -> None:
        """An injected tool can refuse a call before running it (its arguments
        failed validation); the stream's failed status is the only record."""
        tools = FakeAgentTools()

        await closing_messages(
            tools,
            tool_result_chunk(BandTool.SEND_MESSAGE, ToolStatus.FAILED),
            text("I posted the answer."),
            records_tool_effects=False,
        )

        assert tools.messages_sent == []
        assert not tools.turn.complete

    @pytest.mark.asyncio
    async def test_a_refused_custom_reply_tool_still_owes_the_reply(self) -> None:
        """A custom tool declared ``REPLY`` can be refused upstream (its
        arguments failed the MCP schema) before its handler runs."""
        tools = FakeAgentTools()
        emitter = RoomTurnEmitter(
            tools,
            session_id="s1",
            room_id="room-1",
        )
        async with emitter:
            await emitter.emit(tool_result_chunk("post_answer", ToolStatus.FAILED))
            await emitter.emit(text("I posted the answer."))

        assert not tools.turn.complete

    @pytest.mark.asyncio
    async def test_a_reply_call_that_never_finished_still_owes_the_reply(
        self,
    ) -> None:
        """A reply call seen only as started (its permission was denied, or it
        never reported a result) did not deliver."""
        tools = FakeAgentTools()

        await closing_messages(
            tools,
            tool_call_chunk(BandTool.SEND_MESSAGE, ToolStatus.PENDING),
            text("I posted the answer."),
        )

        assert not tools.turn.complete

    @pytest.mark.asyncio
    async def test_a_denied_reply_permission_still_owes_the_reply(self) -> None:
        tools = FakeAgentTools()
        emitter = RoomTurnEmitter(
            tools,
            session_id="s1",
            room_id="room-1",
            emit=frozenset(),
        )
        async with emitter:
            await emitter.open_permission(
                call=ACPToolCall(
                    tool_call_id="tc-1", name=BandTool.SEND_MESSAGE, arguments={}
                ),
                session_id="s1",
                outcome="denied",
            )
            await emitter.emit(text("I posted the answer."))

        assert not tools.turn.complete


@pytest.mark.parametrize("effect", [None, TurnEffect.ACT, TurnEffect.DECLINE])
async def test_failed_closing_telemetry_does_not_change_successful_effects(
    effect: TurnEffect | None,
) -> None:
    tools = FakeAgentTools()
    if effect is not None:
        tools.turn.record(effect)
    tools.send_event_error = RuntimeError("telemetry unavailable")
    await closing_messages(tools, text("closing narration"))
    assert tools.turn.complete is (effect is not None)
    assert tools.chat == []
