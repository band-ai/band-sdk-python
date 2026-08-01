"""Tool execution and execution reporting, as the native adapters get them.

Both `AnthropicAdapter` and `GeminiAdapter` hand these two pieces to
`NativeToolLoopBackend` instead of running their own loop, so this is the one
place the behaviour lives — and the adapters' own copies, which these tests
used to target, are gone.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from band.core.turn.facade import (
    ExecutionBridgingSink,
    NativeProviderAdapter,
    make_custom_tool_executor,
)
from band.core.turn.native import NativeToolLoopBackend
from band.core.contracts import (
    ToolCallEvent,
    ToolResultEvent,
    ToolStatus,
    TurnEventKind,
)
from band.core.run.stream import AgentStream
from band.runtime.tools import BAND_SEND_MESSAGE, ToolCallOutcome
from band.testing import FakeAgentTools
from tests.core.contractsupport import EchoModelProvider, agent_input


class CityInput(BaseModel):
    """Look up weather."""

    city: str


def call(name: str, **arguments: Any) -> SimpleNamespace:
    return SimpleNamespace(id="call-1", name=name, arguments=arguments)


def context_for(tools: Any) -> SimpleNamespace:
    return SimpleNamespace(tools=tools)


class TestCustomToolExecutor:
    @pytest.mark.asyncio
    async def test_a_custom_tool_wins_over_the_platform_tool_of_that_name(self) -> None:
        """Otherwise a developer cannot override a built-in by name."""
        reached_platform = False

        class Tools(FakeAgentTools):
            async def execute_tool_call(self, tool_name: str, arguments: dict) -> Any:
                nonlocal reached_platform
                reached_platform = True
                return "platform"

        async def handler(args: CityInput) -> str:
            return f"custom:{args.city}"

        execute = make_custom_tool_executor([(CityInput, handler)])

        outcome = await execute(
            context_for(Tools(room_id="room-1")), call("city", city="Lisbon")
        )

        assert outcome.value == "custom:Lisbon"
        assert outcome.ok is True
        assert not reached_platform

    @pytest.mark.asyncio
    async def test_an_unknown_name_falls_through_to_the_platform(self) -> None:
        tools = FakeAgentTools(room_id="room-1")
        execute = make_custom_tool_executor([])

        outcome = await execute(context_for(tools), call("band_lookup_peers", page=1))

        assert outcome.ok is True

    @pytest.mark.asyncio
    async def test_a_failed_platform_post_stays_failed(self) -> None:
        class FailedPostTools(FakeAgentTools):
            async def execute_tool_call_structured(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolCallOutcome:
                return ToolCallOutcome(
                    value="post failed", ok=False, error_message="post failed"
                )

        execute = make_custom_tool_executor([])

        outcome = await execute(
            context_for(FailedPostTools(room_id="room-1")),
            call("band_send_message", content="hello"),
        )

        assert outcome.ok is False
        assert outcome.error_message == "post failed"

    @pytest.mark.asyncio
    async def test_a_raising_custom_tool_reports_failure_not_a_crash(self) -> None:
        """The model has to see the failure to recover from it."""

        async def handler(args: CityInput) -> str:
            raise RuntimeError("upstream down")

        execute = make_custom_tool_executor([(CityInput, handler)])

        outcome = await execute(
            context_for(FakeAgentTools(room_id="room-1")),
            call("city", city="Lisbon"),
        )

        assert outcome.ok is False
        assert "upstream down" in outcome.value

    @pytest.mark.asyncio
    async def test_bad_custom_tool_arguments_name_the_tool_and_the_field(self) -> None:
        """A raw validation dump is not something a model can act on.

        ``execute_custom_tool`` already turns the ValidationError into a
        formatted ValueError, so this arrives on the generic failure path —
        what matters is that the text stays actionable.
        """

        async def handler(args: CityInput) -> str:
            return args.city

        execute = make_custom_tool_executor([(CityInput, handler)])

        outcome = await execute(
            context_for(FakeAgentTools(room_id="room-1")), call("city")
        )

        assert outcome.ok is False
        assert "Invalid arguments for city" in outcome.value
        assert "city" in outcome.value

    @pytest.mark.asyncio
    async def test_a_platform_validation_error_is_formatted_for_the_model(
        self,
    ) -> None:
        """Errors that arrive still un-formatted are rendered the same way.

        Every adapter shares one formatter, so a raw ``ValidationError`` reads
        identically no matter which provider drove the turn.
        """

        class Tools(FakeAgentTools):
            async def execute_tool_call_structured(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> ToolCallOutcome:
                CityInput.model_validate(arguments)
                return ToolCallOutcome(value="unreachable", ok=True)

        execute = make_custom_tool_executor([])

        outcome = await execute(context_for(Tools(room_id="room-1")), call("band_x"))

        assert outcome.ok is False
        assert outcome.value == "Invalid arguments for band_x: city: Field required"
        assert outcome.error_message == outcome.value

    @pytest.mark.asyncio
    async def test_a_non_string_result_reaches_the_model_as_json(self) -> None:
        async def handler(args: CityInput) -> dict[str, Any]:
            return {"city": args.city, "temp": 21}

        execute = make_custom_tool_executor([(CityInput, handler)])

        outcome = await execute(
            context_for(FakeAgentTools(room_id="room-1")),
            call("city", city="Lisbon"),
        )

        assert json.loads(outcome.value) == {"city": "Lisbon", "temp": 21}


class TestExecutionBridgingSink:
    @pytest.mark.asyncio
    async def test_enabled_mirrors_calls_and_results_into_the_room(self) -> None:
        tools = FakeAgentTools(room_id="room-1")
        sink = ExecutionBridgingSink(tools=tools, enabled=True)

        await sink.emit(
            ToolCallEvent(
                tool_name="band_lookup_peers",
                tool_call_id="call-1",
                arguments={"page": 1},
            )
        )
        await sink.emit(
            ToolResultEvent(
                tool_name="band_lookup_peers",
                tool_call_id="call-1",
                content="two peers",
                status=ToolStatus.COMPLETED,
            )
        )

        posted = [
            (event["message_type"], json.loads(event["content"]))
            for event in tools.events_sent
        ]
        assert [kind for kind, _ in posted] == ["tool_call", "tool_result"]
        assert posted[0][1] == {
            "name": "band_lookup_peers",
            "args": {"page": 1},
            "tool_call_id": "call-1",
        }
        assert posted[1][1]["is_error"] is False

    @pytest.mark.asyncio
    async def test_a_failed_call_is_marked_as_an_error(self) -> None:
        tools = FakeAgentTools(room_id="room-1")
        sink = ExecutionBridgingSink(tools=tools, enabled=True)

        await sink.emit(
            ToolResultEvent(
                tool_name="band_send_message",
                tool_call_id="call-1",
                content="boom",
                status=ToolStatus.FAILED,
            )
        )

        assert json.loads(tools.events_sent[0]["content"])["is_error"] is True

    @pytest.mark.asyncio
    async def test_disabled_posts_nothing_but_still_records(self) -> None:
        tools = FakeAgentTools(room_id="room-1")
        sink = ExecutionBridgingSink(tools=tools, enabled=False)

        await sink.emit(
            ToolCallEvent(tool_name="band_lookup_peers", tool_call_id="call-1")
        )

        assert tools.events_sent == []
        assert len(sink.events) == 1

    @pytest.mark.asyncio
    async def test_a_room_that_refuses_the_event_does_not_fail_the_turn(self) -> None:
        """Narration is best-effort: losing it must not lose the tool call."""

        class RefusingTools(FakeAgentTools):
            async def send_event(self, **kwargs: Any) -> Any:
                raise RuntimeError("403 Forbidden")

        sink = ExecutionBridgingSink(
            tools=RefusingTools(room_id="room-1"), enabled=True
        )

        await sink.emit(
            ToolCallEvent(tool_name="band_lookup_peers", tool_call_id="call-1")
        )


class _EchoFacade(NativeProviderAdapter[Any, list[Any]]):
    """The smallest adapter shaped like ``AnthropicAdapter`` / ``GeminiAdapter``."""

    def __init__(self, provider: Any) -> None:
        super().__init__()
        self._backend = NativeToolLoopBackend(provider=provider)

    def _build_tools(self, tools: Any) -> list[Any]:
        del tools
        return []

    def _seed_session(self, history: Any) -> list[Any]:
        del history
        return []


class TestFacadeRunContext:
    @pytest.mark.asyncio
    async def test_a_provider_turn_is_observable_on_the_published_stream(self) -> None:
        """A façade's tool loop emits onto the outer turn's sink, not its own.

        ``AgentStream.observe`` is the only view of a turn's events; a façade
        that hands its loop a private sink makes every provider adapter — the
        v2 story — unobservable, while ACP turns show up fine.
        """
        tools = FakeAgentTools(room_id="room-facade")
        adapter = _EchoFacade(EchoModelProvider(tool_name=BAND_SEND_MESSAGE))
        stream = AgentStream.observe(
            adapter,
            agent_input(tools, content="hi"),
            tools=tools,
        )
        async with stream:
            kinds = [envelope.event.kind async for envelope in stream]

        # The loop narrates tool rounds; the reply itself is posted to the room
        # rather than emitted, so no text event is expected here.
        assert kinds == [TurnEventKind.TOOL_CALL, TurnEventKind.TOOL_RESULT]
