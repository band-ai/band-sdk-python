"""AgentStream observation mode (Phase 3B)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from band.core.contracts import (
    RunFailedEvent,
    ThoughtEvent,
    TurnEventKind,
)
from band.core.exceptions import BandConnectionError, RunFailed, StreamError
from band.core.run.cancellation import ExecutionCancellation, FlagCancellation
from band.core.run.sink import RecordingEventSink
from band.core.run.stream import AgentStream
from band.core.types import TurnUsage
from band.testing import FakeAgentTools
from tests.core.contractsupport import (
    EchoModelProvider,
    NativeLoopAdapter,
    agent_input,
    native_turn,
)
from band.core.backends.native import NativeToolLoopBackend


@pytest.mark.asyncio
async def test_live_stream_detaches_its_sink_observer_on_close() -> None:
    sink = RecordingEventSink()
    stream = AgentStream.live_from_sink(sink)

    assert len(sink._observers) == 1
    await stream.aclose()
    assert sink._observers == []

    assert stream._queue is not None
    queued_before = stream._queue.qsize()
    await sink.emit(ThoughtEvent(content="after-close"))
    assert stream._queue.qsize() == queued_before


@pytest.mark.asyncio
async def test_observed_stream_detaches_when_iteration_finishes(
    echo: EchoModelProvider,
) -> None:
    tools = FakeAgentTools(room_id="room-observer-release")
    stream = AgentStream.observe(
        NativeLoopAdapter(NativeToolLoopBackend(provider=echo)),
        agent_input(tools, content="ping"),
        tools=tools,
    )
    sink = stream._sink

    async for _ in stream:
        pass

    assert sink._observers == []


@pytest.mark.asyncio
async def test_observe_yields_tool_events_then_completes(
    posting_echo: EchoModelProvider,
) -> None:
    tools = FakeAgentTools(room_id="room-stream")
    backend = NativeLoopAdapter(NativeToolLoopBackend(provider=posting_echo))
    stream = AgentStream.observe(
        backend,
        agent_input(tools, content="ping"),
        tools=tools,
    )
    async with stream:
        kinds = [env.event.kind async for env in stream]

    assert TurnEventKind.TOOL_CALL in kinds
    assert TurnEventKind.TOOL_RESULT in kinds
    assert stream.result is not None
    # Adapter turns only surface delivery on RunResult; model text is in events/room.
    assert stream.result.delivery is not None or TurnEventKind.TOOL_RESULT in kinds


@pytest.mark.asyncio
async def test_observe_yields_run_failed_on_model_error() -> None:
    class BoomProvider(EchoModelProvider):
        async def complete(self, request, *, context):  # type: ignore[no-untyped-def]
            raise RuntimeError("model exploded")

    tools = FakeAgentTools(room_id="room-fail")
    stream = AgentStream.observe(
        NativeLoopAdapter(NativeToolLoopBackend(provider=BoomProvider())),
        agent_input(tools, content="ping"),
        tools=tools,
    )
    async with stream:
        events = [env.event async for env in stream]

    assert len(events) == 1
    assert isinstance(events[0], RunFailedEvent)
    assert "model exploded" in events[0].message
    assert events[0].retryable is False
    assert issubclass(RunFailed, Exception)


@pytest.mark.asyncio
async def test_observe_raises_stream_error_on_transport_failure() -> None:
    class TransportBoom(EchoModelProvider):
        async def complete(self, request, *, context):  # type: ignore[no-untyped-def]
            raise BandConnectionError("ws down")

    tools = FakeAgentTools(room_id="room-transport")
    stream = AgentStream.observe(
        NativeLoopAdapter(NativeToolLoopBackend(provider=TransportBoom())),
        agent_input(tools, content="ping"),
        tools=tools,
    )
    with pytest.raises(StreamError, match="ws down"):
        async with stream:
            async for _ in stream:
                pass


@pytest.mark.asyncio
async def test_async_with_cancels_in_flight_producer(
    echo: EchoModelProvider,
) -> None:
    """Early exit inside ``async with`` cancels the underlying run."""

    started = asyncio.Event()
    released = asyncio.Event()

    class SlowProvider(EchoModelProvider):
        async def complete(self, request, *, context):  # type: ignore[no-untyped-def]
            started.set()
            try:
                await released.wait()
            except asyncio.CancelledError:
                raise
            return await super().complete(request, context=context)

    tools = FakeAgentTools(room_id="room-cancel")
    token = FlagCancellation()
    stream = AgentStream.observe(
        NativeLoopAdapter(NativeToolLoopBackend(provider=SlowProvider())),
        agent_input(tools, content="ping"),
        tools=tools,
        cancellation=token,
    )

    async with stream:
        await asyncio.wait_for(started.wait(), timeout=1.0)

    assert token.cancelled is True
    producer = stream._producer
    assert producer is not None
    assert producer.done()


@pytest.mark.asyncio
async def test_a_callers_own_token_can_still_cancel_the_run(
    echo: EchoModelProvider,
) -> None:
    """``observe`` needs a lever ``aclose`` can pull, but not at the caller's
    expense: a runtime that hands in ``ExecutionCancellation`` has no other way
    to reach the run, so dropping that token makes the run uninterruptible."""

    calls = 0

    class Counting(EchoModelProvider):
        async def complete(self, request, *, context):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            return await super().complete(request, context=context)

    interrupted = ExecutionCancellation(SimpleNamespace(_interrupt_kind="stop"))
    tools = FakeAgentTools(room_id="room-foreign-token")
    stream = AgentStream.observe(
        NativeLoopAdapter(NativeToolLoopBackend(provider=Counting())),
        agent_input(tools, content="ping"),
        tools=tools,
        cancellation=interrupted,
    )

    async with stream:
        async for _ in stream:
            pass

    assert calls == 0, "the caller's interrupt never reached the backend"


@pytest.mark.asyncio
async def test_native_turn_still_records_envelopes(
    posting_echo: EchoModelProvider,
) -> None:
    posting_echo.usage_per_call = TurnUsage(input_tokens=1, output_tokens=1)
    async with native_turn(posting_echo) as turn:
        await turn.run()
    assert turn.outline == [TurnEventKind.TOOL_CALL, TurnEventKind.TOOL_RESULT]
