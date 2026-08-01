"""ACP turns dual-write onto the published ``AgentStream`` (Phase 3B)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.core.backends.adapter import SimpleAdapterBackend
from band.core.backends.observing import ObservingTools
from band.core.contracts import (
    RunFailedEvent,
    TurnEventKind,
)
from band.core.run.sink import RecordingEventSink
from band.core.run.stream import AgentStream
from band.core.simple_adapter import SimpleAdapter
from band.core.types import PlatformMessage
from band.integrations.acp.room_emitter import RoomTurnEmitter, chunk_to_turn_event
from band.integrations.acp.types import ChunkType, CollectedChunk, ToolStatus
from band.testing import FakeAgentTools
from tests.core.contractsupport import agent_input, turn_tools


class _PassthroughConverter:
    def convert(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(raw)

    def set_agent_name(self, name: str) -> None:
        del name


def _chunk(
    chunk_type: ChunkType,
    content: str,
    *,
    tool_call_id: str = "tc-1",
    status: ToolStatus | None = None,
) -> CollectedChunk:
    metadata: dict[str, Any] = {"tool_call_id": tool_call_id}
    if status is not None:
        metadata["status"] = status
    if chunk_type is ChunkType.TOOL_CALL:
        metadata["raw_input"] = {"x": 1}
    return CollectedChunk(chunk_type=chunk_type, content=content, metadata=metadata)


@pytest.mark.asyncio
async def test_room_emitter_dual_writes_chunks_to_bound_sink() -> None:
    tools = FakeAgentTools(room_id="room-acp-stream")
    sink = RecordingEventSink()
    async with RoomTurnEmitter(
        turn_tools(tools, events=sink),
        mentions=[{"id": "u1", "name": "User"}],
        session_id="sess-1",
        room_id=tools.room_id,
    ) as emitter:
        await emitter.emit(_chunk(ChunkType.THOUGHT, "thinking"))
        await emitter.emit(
            _chunk(
                ChunkType.TOOL_CALL,
                "band_lookup_peers",
                status=ToolStatus.IN_PROGRESS,
            )
        )
        await emitter.emit(
            _chunk(
                ChunkType.TOOL_RESULT,
                "ok",
                status=ToolStatus.COMPLETED,
            )
        )
        await emitter.emit(_chunk(ChunkType.TEXT, "hello"))

    kinds = [env.event.kind for env in sink.events]
    assert kinds == [
        TurnEventKind.THOUGHT,
        TurnEventKind.TOOL_CALL,
        TurnEventKind.TOOL_RESULT,
        TurnEventKind.TEXT_DELTA,
    ]
    # Room still got narration; text held until close then posted.
    assert any(e.get("message_type") == "thought" for e in tools.events_sent)
    assert any(m.get("content") == "hello" for m in tools.messages_sent)


@pytest.mark.asyncio
async def test_an_in_process_room_post_suppresses_the_text_fallback() -> None:
    """An ACP turn's Band tools can run either side of the process boundary.

    An in-process one leaves no `tool_call` in the session-update stream, so
    the chunk scan cannot see it — only the observer the backend wrapped the
    turn's tools in. Without that second record the turn's held text would be
    relayed on top of the message the tool already posted.
    """
    inner = FakeAgentTools(room_id="room-acp-inproc")
    tools = ObservingTools(_inner=inner)

    async with RoomTurnEmitter(
        tools,
        mentions=[{"id": "u1", "name": "User"}],
        session_id="sess-inproc",
        room_id=inner.room_id,
    ) as emitter:
        await tools.execute_tool_call_structured(
            "band_send_message", {"content": "posted by the tool"}
        )
        await emitter.emit(_chunk(ChunkType.TEXT, "I posted it for you."))

    assert [call["tool_name"] for call in inner.tool_calls] == ["band_send_message"]
    assert inner.messages_sent == [], (
        "the tool already spoke; the held text must not be relayed too"
    )


@pytest.mark.asyncio
async def test_room_emitter_observe_failure_yields_run_failed() -> None:
    tools = FakeAgentTools(room_id="room-acp-fail")
    sink = RecordingEventSink()
    with pytest.raises(RuntimeError, match="acp crashed"):
        async with RoomTurnEmitter(
            turn_tools(tools, events=sink),
            mentions=[],
            session_id="sess-fail",
            room_id=tools.room_id,
        ) as emitter:
            await emitter.emit(_chunk(ChunkType.THOUGHT, "before boom"))
            raise RuntimeError("acp crashed")

    kinds = [env.event.kind for env in sink.events]
    assert kinds == [
        TurnEventKind.THOUGHT,
        TurnEventKind.ERROR,
        TurnEventKind.RUN_FAILED,
    ]
    failed = sink.events[-1].event
    assert isinstance(failed, RunFailedEvent)
    assert "acp crashed" in failed.message
    assert failed.error_type == "RuntimeError"


@pytest.mark.asyncio
async def test_agent_stream_observe_sees_acp_emitter_through_shim() -> None:
    """``AgentStream.observe`` + ``SimpleAdapterBackend`` binds the ACP sink."""

    class _AcpLikeAdapter(SimpleAdapter[list[dict[str, Any]]]):
        def __init__(self) -> None:
            super().__init__(history_converter=_PassthroughConverter())

        async def on_message(
            self,
            msg: PlatformMessage,
            tools: Any,
            history: list[dict[str, Any]],
            participants_msg: str | None,
            contacts_msg: str | None,
            *,
            is_session_bootstrap: bool,
            room_id: str,
        ) -> None:
            del history, participants_msg, contacts_msg, is_session_bootstrap
            async with RoomTurnEmitter(
                tools,
                mentions=[{"id": msg.sender_id, "name": "User"}],
                session_id="sess-observe",
                room_id=room_id,
            ) as emitter:
                await emitter.emit(_chunk(ChunkType.THOUGHT, "via observe"))
                await emitter.emit(_chunk(ChunkType.TEXT, "reply"))

        async def on_cleanup(self, room_id: str) -> None:
            del room_id

    tools = FakeAgentTools(room_id="room-observe")
    backend = SimpleAdapterBackend(_AcpLikeAdapter())
    request = agent_input(tools, content="hi")
    # agent_input already has a message; observe uses backend.run
    stream = AgentStream.observe(backend, request, tools=tools)
    async with stream:
        kinds = [env.event.kind async for env in stream]

    assert kinds == [TurnEventKind.THOUGHT, TurnEventKind.TEXT_DELTA]


class _DispatchedAcpAdapter(SimpleAdapter[list[dict[str, Any]]]):
    """An ACP-shaped adapter whose chunks arrive on a long-lived other task.

    Faithful to the real client: the connection's dispatcher task is spawned by
    ``_ensure_connection`` — before any turn — and every session_update it reads
    is handed to whichever turn's emitter is current. So the emitter is built on
    the turn's task but ``emit`` is only ever awaited on the dispatcher's.
    """

    def __init__(self) -> None:
        super().__init__(history_converter=_PassthroughConverter())
        self._handoff: asyncio.Queue[Any] = asyncio.Queue()
        self._drained: asyncio.Queue[None] = asyncio.Queue()
        self._dispatcher = asyncio.create_task(self._dispatch())

    async def _dispatch(self) -> None:
        while True:
            emit, chunks = await self._handoff.get()
            for chunk in chunks:
                await emit(chunk)
            self._drained.put_nowait(None)

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: Any,
        history: list[dict[str, Any]],
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        del history, participants_msg, contacts_msg, is_session_bootstrap
        async with RoomTurnEmitter(
            tools,
            mentions=[{"id": msg.sender_id, "name": "User"}],
            session_id="sess-dispatched",
            room_id=room_id,
        ) as emitter:
            self._handoff.put_nowait(
                (
                    emitter.emit,
                    [
                        _chunk(ChunkType.THOUGHT, "thinking"),
                        _chunk(ChunkType.TEXT, msg.content),
                    ],
                )
            )
            await asyncio.wait_for(self._drained.get(), timeout=1.0)

    async def on_cleanup(self, room_id: str) -> None:
        del room_id

    async def cleanup_all(self) -> None:
        self._dispatcher.cancel()


@pytest.mark.asyncio
async def test_chunks_dispatched_on_another_task_reach_the_turn_sink() -> None:
    """A turn's sink must be reachable from the task that reports its chunks.

    The dispatcher predates the turn, so anything scoped to the turn's own task
    is invisible to it — and since every chunk is reported there, the whole turn
    would go unobserved.
    """
    tools = FakeAgentTools(room_id="room-dispatched")
    adapter = _DispatchedAcpAdapter()
    stream = AgentStream.observe(
        SimpleAdapterBackend(adapter),
        agent_input(tools, content="hi"),
        tools=tools,
    )
    async with stream:
        kinds = [env.event.kind async for env in stream]
    await adapter.cleanup_all()

    assert kinds == [TurnEventKind.THOUGHT, TurnEventKind.TEXT_DELTA]


@pytest.mark.asyncio
async def test_each_turn_is_observed_on_its_own_sink() -> None:
    """One turn's sink must not capture a later turn's events.

    The dispatcher serves every turn of the connection, so a sink it reaches by
    anything other than the turn in hand pins the first turn's sink forever:
    turn two reports into a sink whose consumer is already gone.
    """
    tools = FakeAgentTools(room_id="room-two-turns")
    backend = SimpleAdapterBackend(_DispatchedAcpAdapter())

    async def observed(content: str) -> list[TurnEventKind]:
        stream = AgentStream.observe(
            backend, agent_input(tools, content=content), tools=tools
        )
        async with stream:
            return [env.event.kind async for env in stream]

    first = await observed("first")
    second = await observed("second")
    await backend.aclose()

    assert first == second == [TurnEventKind.THOUGHT, TurnEventKind.TEXT_DELTA]
    assert [m.get("content") for m in tools.messages_sent] == ["first", "second"]


@pytest.mark.asyncio
async def test_consumer_crash_cancels_bound_acp_turn() -> None:
    """Leaving ``async with AgentStream`` cancels an in-flight ACP-like turn."""

    started = asyncio.Event()
    cancelled = asyncio.Event()

    class _SlowAdapter(SimpleAdapter[list[dict[str, Any]]]):
        def __init__(self) -> None:
            super().__init__(history_converter=_PassthroughConverter())

        async def on_message(
            self,
            msg: PlatformMessage,
            tools: Any,
            history: list[dict[str, Any]],
            participants_msg: str | None,
            contacts_msg: str | None,
            *,
            is_session_bootstrap: bool,
            room_id: str,
        ) -> None:
            del msg, tools, history, participants_msg, contacts_msg
            del is_session_bootstrap, room_id
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def on_cleanup(self, room_id: str) -> None:
            del room_id

    tools = FakeAgentTools(room_id="room-cancel-acp")
    stream = AgentStream.observe(
        SimpleAdapterBackend(_SlowAdapter()),
        agent_input(tools, content="hi"),
        tools=tools,
    )
    async with stream:
        await asyncio.wait_for(started.wait(), timeout=1.0)

    await asyncio.wait_for(cancelled.wait(), timeout=1.0)


def test_chunk_to_turn_event_maps_known_types() -> None:
    thought = chunk_to_turn_event(_chunk(ChunkType.THOUGHT, "t"))
    assert thought is not None and thought.kind is TurnEventKind.THOUGHT
    text = chunk_to_turn_event(_chunk(ChunkType.TEXT, "x"))
    assert text is not None and text.kind is TurnEventKind.TEXT_DELTA
    assert chunk_to_turn_event(_chunk(ChunkType.TEXT, "")) is None
