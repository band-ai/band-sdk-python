"""Explicit declines terminate at the native Anthropic tool-batch boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any

import httpx2
import pytest
import pytest_asyncio
from anthropic import AsyncAnthropic

from band.adapters.anthropic import AnthropicAdapter, AnthropicAdapterConfig
from band.core.types import Emit, MessageType, PlatformMessage, ToolEventKey
from band.runtime.tools import BandTool, ToolCallOutcome
from band.runtime.tools.agent import AgentTools
from band.testing import FakeAgentTools
from band.testing.fake_tools import events_of_type, reported_failures
from tests.adapters.usage_events import recorded_usage_payloads


class NativeTools(FakeAgentTools, AgentTools):
    """Use real registry dispatch with the in-memory platform methods."""

    execute_tool_call = AgentTools.execute_tool_call
    execute_tool_call_structured = AgentTools.execute_tool_call_structured
    get_anthropic_tool_schemas = AgentTools.get_anthropic_tool_schemas
    get_tool_schemas = AgentTools.get_tool_schemas


def tool(name: BandTool, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"type": "tool_use", "name": name, "input": arguments, "id": call_id}


def decline(**arguments: Any) -> dict[str, Any]:
    return tool(BandTool.NO_REPLY, arguments, "decline")


def participants() -> dict[str, Any]:
    return tool(BandTool.GET_PARTICIPANTS, {}, "read")


def reply(content: str) -> dict[str, Any]:
    return tool(
        BandTool.SEND_MESSAGE, {"content": content, "mentions": ["user"]}, "reply"
    )


def response(*tools: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "native-response",
        "type": "message",
        "role": "assistant",
        "model": "fixture",
        "content": list(tools),
        "stop_reason": "tool_use" if tools else "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class NativePeer:
    responses: list[dict[str, Any]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def responds_with(self, *responses: dict[str, Any]) -> None:
        self.responses.extend(responses)

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        assert self.responses, "adapter made an unnecessary provider request"
        return httpx2.Response(200, json=self.responses.pop(0))

    def tool_results(self, request_index: int) -> list[ToolResult]:
        return [
            ToolResult(part["tool_use_id"], part["content"], part["is_error"])
            for message in self.requests[request_index]["messages"]
            if isinstance(message["content"], list)
            for part in message["content"]
            if part["type"] == "tool_result"
        ]

    def failed_tools(self, request_index: int) -> list[str]:
        return [
            result.call_id
            for result in self.tool_results(request_index)
            if result.is_error
        ]


@dataclass
class NativeTurn:
    adapter: AnthropicAdapter
    peer: NativePeer
    message: PlatformMessage

    async def run(
        self,
        tools: NativeTools | None = None,
        *,
        message: PlatformMessage | None = None,
        bootstrap: bool = True,
    ) -> NativeTools:
        tools = tools if tools is not None else NativeTools()
        message = message if message is not None else self.message
        await self.adapter.run_judged_turn(
            msg=message,
            tools=tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=bootstrap,
            room_id=message.room_id,
        )
        return tools


@pytest_asyncio.fixture(loop_scope="function")
async def native_turn(
    sample_platform_message: PlatformMessage,
) -> AsyncIterator[NativeTurn]:
    peer = NativePeer()
    adapter = AnthropicAdapter(
        AnthropicAdapterConfig(provider_key="fixture"),
        emit={Emit.TOOL_CALLS, Emit.USAGE},
    )
    await adapter.client.close()
    async with AsyncAnthropic(
        api_key="fixture",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(peer.handle)),
    ) as client:
        adapter.client = client
        await adapter.on_started("Fixture", "Native tool-loop test")
        yield NativeTurn(adapter, peer, sample_platform_message)


def result_names(tools: FakeAgentTools) -> list[str]:
    return [
        json.loads(event["content"])[ToolEventKey.NAME]
        for event in events_of_type(tools, MessageType.TOOL_RESULT)
    ]


@pytest.mark.asyncio
async def test_successful_decline_finishes_without_another_provider_request(
    native_turn: NativeTurn,
) -> None:
    peer = native_turn.peer
    peer.responds_with(response(decline(reason="FYI")))
    tools = await native_turn.run()

    assert len(peer.requests) == 1
    assert tools.turn.complete
    assert tools.messages_sent == []
    assert reported_failures(tools) == []
    assert result_names(tools) == [BandTool.NO_REPLY]
    assert recorded_usage_payloads(tools) == [
        {
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
    ]


class RejectedDeclineTools(NativeTools):
    def __init__(self, rejection: Any) -> None:
        super().__init__()
        self.rejection = rejection

    async def execute_tool_call_structured(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolCallOutcome:
        if tool_name == BandTool.NO_REPLY:
            if isinstance(self.rejection, Exception):
                raise self.rejection
            return ToolCallOutcome(value=self.rejection, ok=True)
        return await super().execute_tool_call_structured(tool_name, arguments)


@pytest.mark.parametrize(
    "rejection",
    [{"ok": False}, "Error: decline rejected", RuntimeError("decline rejected")],
    ids=["failed-output", "error-text", "raised"],
)
@pytest.mark.asyncio
async def test_failed_decline_allows_a_later_reply(
    rejection: Any, native_turn: NativeTurn
) -> None:
    peer = native_turn.peer
    peer.responds_with(
        response(decline()),
        response(reply("recovered")),
        response(),
    )
    tools = await native_turn.run(RejectedDeclineTools(rejection))

    assert len(peer.requests) == 3
    assert tools.chat == ["recovered"]
    assert reported_failures(tools) == []
    assert result_names(tools) == [BandTool.NO_REPLY, BandTool.SEND_MESSAGE]
    assert peer.failed_tools(1) == ["decline"]


@pytest.mark.asyncio
async def test_invalid_decline_arguments_do_not_end_the_turn(
    native_turn: NativeTurn,
) -> None:
    peer = native_turn.peer
    peer.responds_with(
        response(decline(reason=42)),
        response(reply("recovered")),
        response(),
    )
    tools = await native_turn.run()

    assert len(peer.requests) == 3
    (rejected,) = peer.tool_results(1)
    assert rejected.content.startswith("Invalid arguments for")
    assert peer.failed_tools(1) == ["decline"]
    assert tools.chat == ["recovered"]
    assert reported_failures(tools) == []


@pytest.mark.asyncio
async def test_decline_preserves_batch_results_and_replay_on_the_next_turn(
    native_turn: NativeTurn,
) -> None:
    peer = native_turn.peer
    peer.responds_with(
        response(
            decline(),
            participants(),
        ),
        response(reply("new turn")),
        response(),
    )
    first_tools = await native_turn.run()
    assert len(peer.requests) == 1
    next_tools = await native_turn.run(
        message=replace(native_turn.message, id="next", content="Please reply"),
        bootstrap=False,
    )

    assert result_names(first_tools) == [BandTool.NO_REPLY, BandTool.GET_PARTICIPANTS]
    assert peer.tool_results(1) == [
        ToolResult("decline", '{"status": "no_reply"}'),
        ToolResult("read", "[]"),
    ]
    assert len(peer.requests) == 3
    assert next_tools.chat == ["new turn"]
    assert recorded_usage_payloads(first_tools)[0]["input_tokens"] == 10
    assert recorded_usage_payloads(next_tools)[0]["input_tokens"] == 20


@pytest.mark.asyncio
async def test_successful_reply_and_reads_keep_the_native_loop_running(
    native_turn: NativeTurn,
) -> None:
    peer = native_turn.peer
    peer.responds_with(
        response(reply("progress")),
        response(participants()),
        response(),
    )
    tools = await native_turn.run()

    assert len(peer.requests) == 3
    assert result_names(tools) == [BandTool.SEND_MESSAGE, BandTool.GET_PARTICIPANTS]
    assert reported_failures(tools) == []


@pytest.mark.asyncio
async def test_decline_keeps_effects_already_requested_in_the_same_batch(
    native_turn: NativeTurn,
) -> None:
    peer = native_turn.peer
    peer.responds_with(
        response(
            decline(),
            reply("requested work"),
        )
    )
    tools = await native_turn.run()

    assert len(peer.requests) == 1
    assert tools.chat == ["requested work"]
    assert result_names(tools) == [BandTool.NO_REPLY, BandTool.SEND_MESSAGE]
    assert reported_failures(tools) == []


class BlockingReadTools(NativeTools):
    def __init__(self) -> None:
        super().__init__()
        self.read_started = asyncio.Event()
        self.read_cancelled = asyncio.Event()

    async def get_participants(self) -> list[dict[str, Any]]:
        self.read_started.set()
        try:
            await asyncio.Future[None]()
        except asyncio.CancelledError:
            self.read_cancelled.set()
            raise
        return []


@pytest.mark.asyncio
async def test_cancellation_after_decline_still_cancels_the_unfinished_batch(
    native_turn: NativeTurn,
) -> None:
    peer = native_turn.peer
    peer.responds_with(
        response(
            decline(),
            participants(),
        )
    )
    tools = BlockingReadTools()
    async with asyncio.TaskGroup() as tasks:
        task = tasks.create_task(native_turn.run(tools))
        await asyncio.wait_for(tools.read_started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert tools.read_cancelled.is_set()
    assert len(peer.requests) == 1
    assert result_names(tools) == [BandTool.NO_REPLY]
    assert reported_failures(tools) == []
    assert recorded_usage_payloads(tools) == []
