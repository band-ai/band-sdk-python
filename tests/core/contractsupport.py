"""Fixtures and helpers for AgentBackend contract tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from band.core.backends.observing import ObservingTools
from band.core.run.cancellation import FlagCancellation, NeverCancelled
from band.core.contracts import (
    BackendContext,
    ModelMessage,
    TurnEventKind,
    ModelMessageRole,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    RunResult,
)
from band.core.run.sink import RecordingEventSink
from band.core.backends.native import NativeToolLoopBackend
from band.core.backends.oneshot import run_oneshot_turn
from band.core.protocols import (
    AgentToolsProtocol,
    CancellationToken,
    EventSink,
    ModelContext,
    ModelProvider,
    RunContext,
)
from band.core.run.context import SimpleRunContext
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput, PlatformMessage, TurnUsage
from band.integrations.acp.types import ChunkType, CollectedChunk, ToolStatus
from band.runtime.tools import BAND_LIST_CONTACTS, BAND_SEND_MESSAGE
from band.testing import FakeAgentTools
from tests.core.adapterhelpers import make_agent_input, make_platform_message

ROOM_ID = "room-1"


# --- test provider ---


@dataclass
class EchoModelProvider:
    """Echoes the last user message; optional first-call tool request."""

    tool_name: str | None = None
    tool_arguments: dict[str, Any] = field(default_factory=dict)
    usage_per_call: TurnUsage | None = None
    _calls: int = field(default=0, init=False)

    async def complete(
        self, request: ModelRequest, *, context: ModelContext
    ) -> ModelResponse:
        context.cancellation.throw_if_cancelled()
        self._calls += 1
        if self.tool_name is not None and self._calls == 1:
            return ModelResponse(
                text=None,
                tool_calls=(
                    ModelToolCall(
                        id="call-1",
                        name=self.tool_name,
                        arguments=self.tool_arguments,
                    ),
                ),
                usage=self.usage_per_call,
                stop_reason="tool_use",
            )
        return ModelResponse(
            text=f"echo: {_last_user_text(request.messages)}",
            usage=self.usage_per_call,
            stop_reason="end_turn",
        )

    def default_history_policy(self):
        from band.core.backends.history import DefaultHistoryPolicy

        return DefaultHistoryPolicy()


def _last_user_text(messages: Sequence[ModelMessage]) -> str:
    for msg in reversed(messages):
        match msg:
            case ModelMessage(role=ModelMessageRole.USER, content=content):
                return str(content)
            case _:
                continue
    return ""


# --- fixtures ---


@pytest.fixture
def tools() -> FakeAgentTools:
    return FakeAgentTools(room_id=ROOM_ID)


@pytest.fixture
def sink() -> RecordingEventSink:
    return RecordingEventSink()


@pytest.fixture
def echo() -> EchoModelProvider:
    return EchoModelProvider()


@pytest.fixture
def posting_echo() -> EchoModelProvider:
    return EchoModelProvider(
        tool_name=BAND_SEND_MESSAGE,
        tool_arguments={"content": "hi", "mentions": ["Ada"]},
    )


# --- message / request builders ---


def message(*, content: str = "hello", room_id: str = ROOM_ID) -> PlatformMessage:
    return make_platform_message(
        content,
        room_id=room_id,
        message_id="m1",
        sender_id="u1",
        sender_name="Ada",
    )


def agent_input(
    tools: FakeAgentTools,
    *,
    content: str = "hello",
    participants_msg: str | None = None,
    contacts_msg: str | None = None,
    history: list[dict[str, Any]] | None = None,
) -> AgentInput:
    return make_agent_input(
        content,
        tools=tools,
        raw_history=history,
        participants_msg=participants_msg,
        contacts_msg=contacts_msg,
        is_session_bootstrap=True,
        room_id=tools.room_id,
    )


# --- projections ---


def event_kinds(sink: RecordingEventSink) -> list[TurnEventKind]:
    return [TurnEventKind(envelope.event.kind) for envelope in sink.events]


# --- ACP chunk scenarios ---


def acp_chunk(
    chunk_type: ChunkType,
    content: str,
    *,
    tool_call_id: str = "c1",
    status: ToolStatus,
) -> CollectedChunk:
    return CollectedChunk(
        chunk_type=chunk_type,
        content=content,
        metadata={"tool_call_id": tool_call_id, "status": status},
    )


def acp_completed_post() -> list[CollectedChunk]:
    return [
        acp_chunk(
            ChunkType.TOOL_CALL,
            BAND_SEND_MESSAGE,
            status=ToolStatus.COMPLETED,
        )
    ]


def acp_correlated_post() -> list[CollectedChunk]:
    return [
        acp_chunk(
            ChunkType.TOOL_CALL,
            BAND_SEND_MESSAGE,
            status=ToolStatus.IN_PROGRESS,
        ),
        acp_chunk(ChunkType.TOOL_RESULT, "ok", status=ToolStatus.COMPLETED),
    ]


def acp_uncorrelated_post() -> list[CollectedChunk]:
    """An id-less pending post plus an id-less completed non-posting result.

    Both ids default to ``""``; they must not correlate, or a turn that never
    posted looks delivered and its text fallback is falsely suppressed.
    """
    return [
        acp_chunk(
            ChunkType.TOOL_CALL,
            BAND_SEND_MESSAGE,
            tool_call_id="",
            status=ToolStatus.IN_PROGRESS,
        ),
        acp_chunk(
            ChunkType.TOOL_RESULT, "", tool_call_id="", status=ToolStatus.COMPLETED
        ),
    ]


def acp_failed_post() -> list[CollectedChunk]:
    return [
        acp_chunk(
            ChunkType.TOOL_CALL,
            BAND_SEND_MESSAGE,
            status=ToolStatus.FAILED,
        ),
        acp_chunk(ChunkType.TOOL_RESULT, "boom", status=ToolStatus.FAILED),
    ]


def acp_non_posting_tool() -> list[CollectedChunk]:
    return [
        acp_chunk(
            ChunkType.TOOL_CALL,
            BAND_LIST_CONTACTS,
            status=ToolStatus.COMPLETED,
        )
    ]


# --- adapters ---


class PostingAdapter(SimpleAdapter[object]):
    """Posts once via ``band_send_message``."""

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: Any,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        await tools.execute_tool_call_structured(
            BAND_SEND_MESSAGE,
            {"content": "hi", "mentions": ["Ada"]},
        )


@dataclass
class NativeFacadeBackend:
    """Presents a tool loop as an ``AgentBackend``, the way a façade does.

    ``NativeToolLoopBackend`` owns its own session, so it takes what it primes
    a turn with rather than an ``AgentInput`` whose history it would ignore —
    which means it is not an ``AgentBackend``. In production the two provider
    adapters supply that shape; tests that need to observe a bare tool loop
    (``AgentStream.observe``) use this stand-in AgentBackend stand-in for the same reason.
    """

    loop: NativeToolLoopBackend

    async def start(self, context: BackendContext) -> None:
        await self.loop.start(context)

    async def run(self, inp: AgentInput, *, context: RunContext) -> RunResult:
        return await self.loop.run(
            session_id=inp.room_id,
            message=inp.msg,
            context=context,
            participants_context=inp.participants_msg,
            contacts_context=inp.contacts_msg,
        )

    async def close_session(self, session_id: str) -> None:
        await self.loop.close_session(session_id)

    async def aclose(self) -> None:
        await self.loop.aclose()


# --- turn context managers ---


@dataclass
class NativeTurn:
    backend: NativeToolLoopBackend
    tools: FakeAgentTools
    sink: RecordingEventSink
    context: SimpleRunContext

    async def run(self, *, content: str = "ping") -> RunResult:
        return await self.backend.run(
            session_id=self.tools.room_id,
            message=message(content=content, room_id=self.tools.room_id),
            context=self.context,
        )

    @property
    def outline(self) -> list[TurnEventKind]:
        return event_kinds(self.sink)


@asynccontextmanager
async def native_turn(
    provider: ModelProvider,
    *,
    tools: FakeAgentTools | None = None,
    sink: RecordingEventSink | None = None,
    cancellation: CancellationToken | None = None,
) -> AsyncIterator[NativeTurn]:
    tools = tools or FakeAgentTools(room_id=ROOM_ID)
    sink = sink or RecordingEventSink()
    context = SimpleRunContext(
        tools=cast(AgentToolsProtocol, tools),
        events=sink,
        cancellation=cancellation or NeverCancelled(),
    )
    yield NativeTurn(
        backend=NativeToolLoopBackend(provider=provider),
        tools=tools,
        sink=sink,
        context=context,
    )


@dataclass
class ShimTurn:
    adapter: SimpleAdapter[object]
    tools: FakeAgentTools
    context: SimpleRunContext

    async def run(self, *, content: str = "hello") -> RunResult:
        from band.core.backends.oneshot import run_adapter_turn

        return await run_adapter_turn(
            self.adapter,
            agent_input(self.tools, content=content),
            context=self.context,
        )


@asynccontextmanager
async def shim_turn(
    adapter: SimpleAdapter[object],
    *,
    tools: FakeAgentTools | None = None,
) -> AsyncIterator[ShimTurn]:
    tools = tools or FakeAgentTools(room_id=ROOM_ID)
    yield ShimTurn(
        adapter=adapter,
        tools=tools,
        context=SimpleRunContext(tools=cast(AgentToolsProtocol, tools)),
    )


@dataclass
class OneshotTurn:
    adapter: SimpleAdapter[object]
    tools: FakeAgentTools
    content: str = "hello"

    async def run(self) -> RunResult:
        return await run_oneshot_turn(
            self.adapter, agent_input(self.tools, content=self.content)
        )


@asynccontextmanager
async def oneshot(
    adapter: SimpleAdapter[object],
    *,
    content: str = "hello",
    tools: FakeAgentTools | None = None,
) -> AsyncIterator[OneshotTurn]:
    tools = tools or FakeAgentTools(room_id=ROOM_ID)
    yield OneshotTurn(
        adapter=adapter,
        tools=tools,
        content=content,
    )


def turn_tools(
    tools: Any,
    *,
    events: EventSink | None = None,
    cancellation: CancellationToken | None = None,
) -> ObservingTools:
    """Tools as an adapter receives them: wrapped in a turn.

    What a turn hands ``on_message``, for tests that drive an adapter directly
    and still need the turn's sink or token reachable.
    """
    return ObservingTools(
        _inner=cast(AgentToolsProtocol, tools),
        turn=SimpleRunContext(
            tools=cast(AgentToolsProtocol, tools),
            events=events or RecordingEventSink(),
            cancellation=cancellation or NeverCancelled(),
        ),
    )


@contextmanager
def cancelled() -> Iterator[FlagCancellation]:
    token = FlagCancellation()
    token.cancel()
    yield token
