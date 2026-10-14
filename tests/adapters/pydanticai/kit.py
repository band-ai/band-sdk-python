"""Tests for PydanticAIAdapter.

Tests for shared adapter behavior (initialization defaults, custom kwargs,
history_converter, on_message callable, cleanup safety) live in
tests/framework_conformance/test_adapter_conformance.py.
This file contains PydanticAI-specific behavior: agent creation, tool registration,
stream event handling, execution reporting, and custom tools.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, NamedTuple
from unittest.mock import AsyncMock, MagicMock

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    InstrumentationSettings,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from band.adapters.pydantic_ai import (
    PydanticAIAdapter,
    PydanticAIAdapterConfig,
    _custom_tool_def_to_callable,
)
from band.core.protocols import (
    AgentToolsProtocol,
)
from band.core.types import (
    MessageType,
    PlatformMessage,
    ToolEventKey,
)
from band.runtime.custom_tools import (
    CustomToolDef,
)
from band.runtime.tools import (
    BandTool,
)
from band.testing import (
    FakeAgentTools,
    events_of_type,
)
from tests.adapters.usage_events import recorded_usage_payloads


def make_stream_events(
    result_messages: list | None = None,
    tool_calls: list[tuple[str, dict, str]] | None = None,
    tool_results: list[tuple[str, str, str]] | None = None,
):
    """Stand in for ``Agent.run_stream_events()``: an async CM over stream events.

    pydantic-ai 2.x makes ``run_stream_events()`` an async context manager that
    yields the event iterator (it starts the run on first iteration and tears the
    background task down on exit), so the fake has to be one too.

    Args:
        result_messages: Messages to return in AgentRunResultEvent
        tool_calls: List of (tool_name, args, tool_call_id) tuples
        tool_results: List of (tool_name, output, tool_call_id) tuples
    """

    async def stream():
        # Emit tool call events
        if tool_calls:
            for tool_name, args, tool_call_id in tool_calls:
                event = MagicMock(spec=FunctionToolCallEvent)
                event.part = MagicMock()
                event.part.tool_name = tool_name
                event.part.args = args
                event.part.args_as_dict = MagicMock(return_value=args)
                event.part.tool_call_id = tool_call_id
                yield event

        # Emit tool result events
        if tool_results:
            for tool_name, output, tool_call_id in tool_results:
                event = MagicMock(spec=FunctionToolResultEvent)
                event.part = MagicMock()
                event.part.tool_name = tool_name  # tool_name is on the part, not event
                event.part.content = output
                event.tool_call_id = tool_call_id
                yield event

        # Always emit final result event
        result_event = MagicMock(spec=AgentRunResultEvent)
        result_event.result = MagicMock()
        result_event.result.all_messages.return_value = result_messages or []
        yield result_event

    @asynccontextmanager
    async def events() -> AsyncIterator[AsyncIterator]:
        yield stream()

    return events()


def make_usage_response(
    inp: int, out: int, parts: list[Any] | None = None
) -> ModelResponse:
    """A ModelResponse carrying explicit usage counts.

    Real construction (fully initialized, so the dataclass stays repr-safe),
    then the usage field is overridden past any frozen/validated assignment.
    """
    response = ModelResponse(parts=parts or [])
    object.__setattr__(
        response, "usage", SimpleNamespace(input_tokens=inp, output_tokens=out)
    )
    return response


@pytest.fixture
def sample_message():
    """Create a sample platform message."""
    return PlatformMessage(
        id="msg-123",
        room_id="room-123",
        content="Hello, agent!",
        sender_id="user-456",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def mock_tools():
    """Create mock AgentToolsProtocol (MagicMock base, AsyncMock methods)."""
    tools = MagicMock()
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.send_failure = AsyncMock(return_value={"status": "sent"})
    tools.add_participant = AsyncMock(return_value={"id": "user-1"})
    tools.remove_participant = AsyncMock(return_value={"status": "removed"})
    tools.lookup_peers = AsyncMock(return_value={"peers": []})
    tools.get_participants = AsyncMock(return_value=[])
    tools.create_chatroom = AsyncMock(return_value="new-room-123")
    return tools


@pytest.fixture
def mock_pydantic_agent():
    """Create a mock Pydantic AI Agent."""
    agent = MagicMock()
    agent._function_tools = {
        "band_send_message": MagicMock(name="band_send_message"),
        "band_send_event": MagicMock(name="band_send_event"),
        "band_add_participant": MagicMock(name="band_add_participant"),
        "band_remove_participant": MagicMock(name="band_remove_participant"),
        "band_lookup_peers": MagicMock(name="band_lookup_peers"),
        "band_get_participants": MagicMock(name="band_get_participants"),
        "band_create_chatroom": MagicMock(name="band_create_chatroom"),
    }
    return agent


class TraceCapture(NamedTuple):
    """A tracer wired to memory, plus the settings that route an agent into it."""

    provider: TracerProvider
    settings: InstrumentationSettings
    exporter: InMemorySpanExporter

    def operations(self) -> list[str]:
        """The exported spans' operation names (``chat``, ``invoke_agent``, ...).

        The full span name carries the model and agent, which the assertions here
        don't care about — the question is only whether the run was traced.
        """
        return [span.name.split()[0] for span in self.exporter.get_finished_spans()]


@pytest.fixture
def trace_capture() -> Iterator[TraceCapture]:
    """Host-owned tracer pipeline, exporting to memory instead of a collector."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        yield TraceCapture(
            provider=provider,
            settings=InstrumentationSettings(tracer_provider=provider),
            exporter=exporter,
        )
    finally:
        provider.shutdown()


@pytest.fixture
def instrument_all_restored() -> Iterator[None]:
    """Undo ``Agent.instrument_all()``; it is process-wide state on the class."""
    previous = Agent._instrument_default
    try:
        yield
    finally:
        Agent.instrument_all(previous)


def message_types(mock_tools: MagicMock) -> list[str]:
    """``message_type`` of every event posted through send_event, in order."""
    return [
        call.kwargs.get("message_type") for call in mock_tools.send_event.call_args_list
    ]


def _reply(text: str) -> FunctionModel:
    """A model that answers in plain text, so a run needs no network or tools."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=text)])

    return FunctionModel(respond)


def _wire_tool_call_args(args: Any) -> dict[str, Any] | str:
    """Shape tool args the way providers send them on the wire (JSON text or object)."""
    if isinstance(args, dict | str):
        return args
    return json.dumps(args)


def _scripted_tool_calls(*calls: tuple[str, Any]) -> FunctionModel:
    """A model that makes ``calls``, one per turn, then answers in plain text.

    A rejected call brings the model straight back here with nothing left
    pending, so it is attempted exactly once — which is what makes "never
    dispatched" observable rather than merely slow.
    """
    pending = list(calls)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if pending:
            name, args = pending.pop(0)
            return ModelResponse(parts=[ToolCallPart(name, _wire_tool_call_args(args))])
        return ModelResponse(parts=[TextPart("done")])

    return FunctionModel(respond)


async def _started_adapter(**features: Any) -> PydanticAIAdapter:
    """A started adapter, i.e. one whose agent is really built."""
    adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="test"), **features)
    await adapter.on_started(agent_name="Probe", agent_description="probe")
    return adapter


def _registered_names(adapter: PydanticAIAdapter) -> set[str]:
    return set(adapter._agent._function_toolset.tools)


async def _call_tool(
    adapter: PydanticAIAdapter, deps: Any, name: str, args: Any
) -> Any:
    """Drive one built-in tool call through pydantic-ai's own execution path.

    Not the tool function directly: validation, argument splatting and the
    retry conversion are pydantic-ai's, and calling the function by hand would
    skip every one of them.
    """
    with adapter._agent.override(model=_scripted_tool_calls((name, args))):
        return await adapter._agent.run("go", deps=deps)


def _parts(result: Any, part_type: type) -> list[Any]:
    return [
        part
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, part_type)
    ]


def _tool_returns(result: Any) -> list[Any]:
    return [part.content for part in _parts(result, ToolReturnPart)]


def _streamed_tool_calls(*calls: tuple[str, dict[str, Any]]) -> FunctionModel:
    """A streaming model (what ``on_message`` drives) making ``calls``, one per
    request, then ending with nothing left to say."""
    pending = list(calls)

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        if pending:
            name, args = pending.pop(0)
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args))}
        else:
            yield ""

    return FunctionModel(stream_function=stream)


class DecliningModel:
    """A native streaming model that keeps declining if asked again."""

    def __init__(self) -> None:
        self.requests = 0
        self.model = FunctionModel(stream_function=self.stream)

    async def stream(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[dict[int, DeltaToolCall]]:
        self.requests += 1
        yield {
            0: DeltaToolCall(
                name=BandTool.NO_REPLY,
                json_args=json.dumps({"reason": "Already answered"}),
                tool_call_id=f"decline-{self.requests}",
            )
        }


def _reply_arguments(message: PlatformMessage, content: str) -> dict[str, Any]:
    return {"content": content, "mentions": [message.sender_name]}


def _tool_feedback(messages: list[ModelMessage]) -> str:
    """Tool results and retry prompts a later model request can read."""
    return "\n".join(
        str(part.content)
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart | RetryPromptPart)
    )


def _history_tool_returns(messages: list[ModelMessage]) -> list[tuple[str, str, Any]]:
    return [
        (part.tool_name, part.tool_call_id, part.content)
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def _emitted_tool_results(tools: FakeAgentTools) -> list[tuple[str, str, str]]:
    results = [
        json.loads(event["content"])
        for event in events_of_type(tools, MessageType.TOOL_RESULT)
    ]
    return [
        (
            result[ToolEventKey.NAME],
            result[ToolEventKey.OUTPUT],
            result[ToolEventKey.TOOL_CALL_ID],
        )
        for result in results
    ]


def _assert_billed_responses(
    tools: FakeAgentTools, messages: list[ModelMessage], *, model_requests: int = 1
) -> None:
    responses = [message for message in messages if isinstance(message, ModelResponse)]
    assert len(responses) == model_requests
    (payload,) = recorded_usage_payloads(tools)
    assert (
        payload["input_tokens"]
        == sum(response.usage.input_tokens for response in responses)
        > 0
    )
    assert (
        payload["output_tokens"]
        == sum(response.usage.output_tokens for response in responses)
        > 0
    )


async def _run_on_message(
    adapter: PydanticAIAdapter, msg: PlatformMessage, tools: FakeAgentTools
) -> None:
    await adapter.on_message(
        msg=msg,
        tools=tools,
        history=[],
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id=tools.room_id,
    )


def make_raising_stream(
    error: BaseException,
    *,
    work: Callable[[], Awaitable[object]] | None = None,
):
    """Run stream (async CM, as 2.x returns) that does ``work``, then raises.

    ``work`` stands in for the tool calls the run made through the room's
    tools, so the turn's ledger sees exactly what those calls did.
    """

    async def stream():
        if work is not None:
            await work()
        raise error
        yield  # an async generator, like the real event stream

    @asynccontextmanager
    async def events() -> AsyncIterator[AsyncIterator]:
        yield stream()

    return events()


def _portable_tool_agent(
    tool_def: CustomToolDef,
) -> Agent[AgentToolsProtocol, str]:
    """A bare agent running one converted ``CustomToolDef`` on pydantic-ai's own path."""
    agent = Agent(TestModel(), deps_type=AgentToolsProtocol, output_type=str)
    agent.tool(_custom_tool_def_to_callable(tool_def))
    return agent


async def _run_portable_tool(
    tool_def: CustomToolDef, tools: FakeAgentTools | None = None
) -> list[Any]:
    """Run one converted ``CustomToolDef`` in a room; return what the model saw."""
    result = await _portable_tool_agent(tool_def).run(
        "go", deps=tools or FakeAgentTools()
    )
    return _tool_returns(result)
