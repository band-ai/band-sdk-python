"""NativeToolLoopBackend: tool loop, delivery evidence, cancellation."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.core.backends.history import DefaultHistoryPolicy
from band.core.backends.facade import make_custom_tool_executor
from band.core.backends.native import NativeToolLoopBackend
from band.core.contracts import (
    ModelResponse,
    ModelToolCall,
    ToolCallEvent,
    ToolResultEvent,
    TurnEventKind,
)
from band.core.run.cancellation import FlagCancellation
from band.core.run.context import SimpleRunContext
from band.core.types import TurnUsage
from band.runtime.tools import BAND_SEND_MESSAGE, ToolCallOutcome
from band.testing import FakeAgentTools
from tests.core.contractsupport import (
    EchoModelProvider,
    cancelled,
    message,
    native_turn,
)

_USAGE_PER_CALL = TurnUsage(input_tokens=1, output_tokens=1)


@pytest.mark.asyncio
async def test_tool_round_emits_call_then_result_and_mints_receipt(
    posting_echo: EchoModelProvider,
) -> None:
    posting_echo.usage_per_call = _USAGE_PER_CALL

    async with native_turn(posting_echo) as turn:
        result = await turn.run()

    assert result.delivery is not None
    assert result.delivery.tool_name == BAND_SEND_MESSAGE
    assert result.usage == _USAGE_PER_CALL + _USAGE_PER_CALL
    assert turn.outline == [TurnEventKind.TOOL_CALL, TurnEventKind.TOOL_RESULT]
    assert turn.sink.events[0].run_id == turn.sink.events[1].run_id
    assert turn.tools.tool_calls[0]["tool_name"] == BAND_SEND_MESSAGE


@pytest.mark.asyncio
async def test_text_only_turn_returns_output_without_delivery(
    echo: EchoModelProvider,
) -> None:
    async with native_turn(echo) as turn:
        result = await turn.run(content="ping")

    assert result.text is not None
    assert "ping" in result.text
    assert result.delivery is None


@pytest.mark.asyncio
async def test_failed_room_post_does_not_mint_delivery_receipt() -> None:
    responses = iter(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        id="post-1",
                        name=BAND_SEND_MESSAGE,
                        arguments={"content": "hello"},
                    ),
                )
            ),
            ModelResponse(text="fallback"),
        ]
    )

    class Provider:
        async def complete(self, request, *, context):  # type: ignore[no-untyped-def]
            return next(responses)

    class FailedPostTools(FakeAgentTools):
        async def execute_tool_call_structured(
            self, tool_name: str, arguments: dict[str, Any]
        ) -> ToolCallOutcome:
            return ToolCallOutcome(value="post failed", ok=False)

    result = await NativeToolLoopBackend(
        provider=Provider(),
        execute_override=make_custom_tool_executor([]),
        history_policy=DefaultHistoryPolicy(),
    ).run(
        session_id="room-failed-post",
        message=message(room_id="room-failed-post"),
        context=SimpleRunContext(tools=FailedPostTools(room_id="room-failed-post")),
    )

    assert result.delivery is None


@pytest.mark.asyncio
async def test_cancelled_run_raises_before_provider_call(
    echo: EchoModelProvider,
    tools: FakeAgentTools,
) -> None:
    with cancelled() as token:
        async with native_turn(echo, tools=tools, cancellation=token) as turn:
            with pytest.raises(asyncio.CancelledError):
                await turn.run()


@pytest.mark.asyncio
async def test_concurrent_rooms_keep_their_own_tool_definitions() -> None:
    """A delayed second model request must retain its own run-local schemas."""

    tool_started = asyncio.Event()
    release_tool = asyncio.Event()
    requests = []
    tool_a = {"name": "tool-a"}
    tool_b = {"name": "tool-b"}

    class Provider:
        async def complete(self, request, *, context):  # type: ignore[no-untyped-def]
            requests.append(request)
            if len(requests) == 1:
                return ModelResponse(
                    tool_calls=(
                        ModelToolCall(id="tool-a-call", name="wait", arguments={}),
                    )
                )
            return ModelResponse(text="done")

    async def execute(_context, _call):  # type: ignore[no-untyped-def]
        tool_started.set()
        await release_tool.wait()
        return ToolCallOutcome(value="ok", ok=True)

    backend = NativeToolLoopBackend(
        provider=Provider(),
        execute_override=execute,
        history_policy=DefaultHistoryPolicy(),
    )
    room_a = asyncio.create_task(
        backend.run(
            session_id="room-a",
            message=message(room_id="room-a"),
            context=SimpleRunContext(tools=FakeAgentTools(room_id="room-a")),
            tools=[tool_a],
        )
    )
    await tool_started.wait()
    await backend.run(
        session_id="room-b",
        message=message(room_id="room-b"),
        context=SimpleRunContext(tools=FakeAgentTools(room_id="room-b")),
        tools=[tool_b],
    )
    release_tool.set()
    await room_a

    assert [request.tools for request in requests] == [[tool_a], [tool_b], [tool_a]]


@pytest.mark.asyncio
async def test_cancellation_stops_remaining_tool_calls() -> None:
    token = FlagCancellation()
    executed: list[str] = []

    class Provider:
        async def complete(self, request, *, context):  # type: ignore[no-untyped-def]
            return ModelResponse(
                tool_calls=(
                    ModelToolCall(id="first-call", name="first", arguments={}),
                    ModelToolCall(id="second-call", name="second", arguments={}),
                )
            )

    async def execute(_context, call):  # type: ignore[no-untyped-def]
        executed.append(call.name)
        token.cancel()
        return ToolCallOutcome(value="ok", ok=True)

    backend = NativeToolLoopBackend(
        provider=Provider(),
        execute_override=execute,
        history_policy=DefaultHistoryPolicy(),
    )
    with pytest.raises(asyncio.CancelledError):
        await backend.run(
            session_id="room-cancel",
            message=message(room_id="room-cancel"),
            context=SimpleRunContext(
                tools=FakeAgentTools(room_id="room-cancel"), cancellation=token
            ),
        )

    assert executed == ["first"]
    assert [
        message.tool_call_id
        for message in backend.session("room-cancel")
        if message.tool_call_id is not None
    ] == ["first-call", "second-call"]


@pytest.mark.asyncio
async def test_cancellation_during_tool_call_emission_stops_execution() -> None:
    token = FlagCancellation()
    executed: list[str] = []

    class Provider:
        async def complete(self, request, *, context):  # type: ignore[no-untyped-def]
            return ModelResponse(
                tool_calls=(ModelToolCall(id="call-1", name="first", arguments={}),)
            )

    class CancellingSink:
        async def emit(self, event: object) -> None:
            if isinstance(event, ToolCallEvent):
                token.cancel()

    async def execute(_context, call):  # type: ignore[no-untyped-def]
        executed.append(call.name)
        return ToolCallOutcome(value="ok", ok=True)

    backend = NativeToolLoopBackend(
        provider=Provider(),
        execute_override=execute,
        history_policy=DefaultHistoryPolicy(),
    )
    with pytest.raises(asyncio.CancelledError):
        await backend.run(
            session_id="room-cancel-during-event",
            message=message(room_id="room-cancel-during-event"),
            context=SimpleRunContext(
                tools=FakeAgentTools(room_id="room-cancel-during-event"),
                cancellation=token,
                events=CancellingSink(),
            ),
        )

    assert executed == []


@pytest.mark.asyncio
async def test_tool_history_survives_result_event_failure() -> None:
    executed: list[str] = []

    class Provider:
        async def complete(self, request, *, context):  # type: ignore[no-untyped-def]
            return ModelResponse(
                tool_calls=(ModelToolCall(id="call-1", name="first", arguments={}),)
            )

    class FailingResultSink:
        async def emit(self, event: object) -> None:
            if isinstance(event, ToolResultEvent):
                raise RuntimeError("event sink failed")

    async def execute(_context, call):  # type: ignore[no-untyped-def]
        executed.append(call.name)
        return ToolCallOutcome(value="ok", ok=True)

    backend = NativeToolLoopBackend(
        provider=Provider(),
        execute_override=execute,
        history_policy=DefaultHistoryPolicy(),
    )
    with pytest.raises(RuntimeError, match="event sink failed"):
        await backend.run(
            session_id="room-event-failure",
            message=message(room_id="room-event-failure"),
            context=SimpleRunContext(
                tools=FakeAgentTools(room_id="room-event-failure"),
                events=FailingResultSink(),
            ),
        )

    assert executed == ["first"]
    assert [
        message.tool_call_id
        for message in backend.session("room-event-failure")
        if message.name == "first"
    ] == ["call-1"]


def test_configuration_requires_valid_policy_and_limits() -> None:
    class MissingPolicyProvider:
        async def complete(self, request, *, context):  # type: ignore[no-untyped-def]
            return ModelResponse(text="done")

    with pytest.raises(TypeError, match="max_tool_rounds"):
        NativeToolLoopBackend(  # type: ignore[arg-type]
            provider=MissingPolicyProvider(), max_tool_rounds="one"
        )
    with pytest.raises(ValueError, match="on_max_rounds"):
        NativeToolLoopBackend(  # type: ignore[arg-type]
            provider=MissingPolicyProvider(), on_max_rounds="ignore"
        )
    with pytest.raises(TypeError, match="default_history_policy"):
        NativeToolLoopBackend(provider=MissingPolicyProvider())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_empty_terminal_text_is_a_text_output() -> None:
    class EmptyProvider:
        async def complete(self, request, *, context):  # type: ignore[no-untyped-def]
            return ModelResponse(text="")

    result = await NativeToolLoopBackend(
        provider=EmptyProvider(), history_policy=DefaultHistoryPolicy()
    ).run(
        session_id="room-empty",
        message=message(room_id="room-empty"),
        context=SimpleRunContext(tools=FakeAgentTools(room_id="room-empty")),
    )

    assert result.text == ""
