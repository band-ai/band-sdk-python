"""Explicit declines terminate at the native Anthropic tool-batch boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic

from band.adapters.anthropic import AnthropicAdapter, AnthropicAdapterConfig
from band.core.types import Emit, PlatformMessage, ToolEventKey
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


@dataclass
class NativePeer:
    responses: list[dict[str, Any]]
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        assert self.responses, "adapter made an unnecessary provider request"
        return httpx2.Response(200, json=self.responses.pop(0))

    def tool_results(self, request_index: int) -> list[dict[str, Any]]:
        return [
            part
            for message in self.requests[request_index]["messages"]
            if isinstance(message["content"], list)
            for part in message["content"]
            if part["type"] == "tool_result"
        ]

    def result_pairs(self, request_index: int) -> list[tuple[str, str]]:
        return [
            (part["tool_use_id"], part["content"])
            for part in self.tool_results(request_index)
        ]

    def failed_tools(self, request_index: int) -> list[str]:
        return [
            part["tool_use_id"]
            for part in self.tool_results(request_index)
            if part["is_error"]
        ]


@asynccontextmanager
async def native_adapter(
    peer: NativePeer,
) -> AsyncIterator[AnthropicAdapter]:
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
        yield adapter


async def run_turn(
    adapter: AnthropicAdapter,
    message: PlatformMessage,
    tools: FakeAgentTools,
    *,
    bootstrap: bool = True,
) -> None:
    await adapter.run_judged_turn(
        msg=message,
        tools=tools,
        history=[],
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=bootstrap,
        room_id=message.room_id,
    )


def result_names(tools: FakeAgentTools) -> list[str]:
    return [
        json.loads(event["content"])[ToolEventKey.NAME]
        for event in events_of_type(tools, "tool_result")
    ]


@pytest.mark.asyncio
async def test_successful_decline_finishes_without_another_provider_request(
    sample_platform_message: PlatformMessage,
) -> None:
    peer = NativePeer([response(tool(BandTool.NO_REPLY, {"reason": "FYI"}, "decline"))])
    tools = NativeTools()
    async with native_adapter(peer) as adapter:
        await run_turn(adapter, sample_platform_message, tools)

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
    rejection: Any, sample_platform_message: PlatformMessage
) -> None:
    peer = NativePeer(
        [
            response(tool(BandTool.NO_REPLY, {}, "decline")),
            response(
                tool(
                    BandTool.SEND_MESSAGE,
                    {"content": "recovered", "mentions": ["user"]},
                    "reply",
                )
            ),
            response(),
        ]
    )
    tools = RejectedDeclineTools(rejection)
    async with native_adapter(peer) as adapter:
        await run_turn(adapter, sample_platform_message, tools)

    assert len(peer.requests) == 3
    assert tools.chat == ["recovered"]
    assert reported_failures(tools) == []
    assert result_names(tools) == [BandTool.NO_REPLY, BandTool.SEND_MESSAGE]
    assert peer.failed_tools(1) == ["decline"]


@pytest.mark.asyncio
async def test_invalid_decline_arguments_do_not_end_the_turn(
    sample_platform_message: PlatformMessage,
) -> None:
    peer = NativePeer(
        [
            response(tool(BandTool.NO_REPLY, {"reason": 42}, "decline")),
            response(
                tool(
                    BandTool.SEND_MESSAGE,
                    {"content": "recovered", "mentions": ["user"]},
                    "reply",
                )
            ),
            response(),
        ]
    )
    tools = NativeTools()
    async with native_adapter(peer) as adapter:
        await run_turn(adapter, sample_platform_message, tools)

    assert len(peer.requests) == 3
    assert peer.result_pairs(1)[0][1].startswith("Invalid arguments for")
    assert peer.failed_tools(1) == ["decline"]
    assert tools.chat == ["recovered"]
    assert reported_failures(tools) == []


@pytest.mark.asyncio
async def test_decline_preserves_batch_results_and_replay_on_the_next_turn(
    sample_platform_message: PlatformMessage,
) -> None:
    peer = NativePeer(
        [
            response(
                tool(BandTool.NO_REPLY, {}, "decline"),
                tool(BandTool.GET_PARTICIPANTS, {}, "read"),
            ),
            response(
                tool(
                    BandTool.SEND_MESSAGE,
                    {"content": "new turn", "mentions": ["user"]},
                    "reply",
                )
            ),
            response(),
        ]
    )
    first_tools, next_tools = NativeTools(), NativeTools()
    async with native_adapter(peer) as adapter:
        await run_turn(adapter, sample_platform_message, first_tools)
        assert len(peer.requests) == 1
        await run_turn(
            adapter,
            replace(sample_platform_message, id="next", content="Please reply"),
            next_tools,
            bootstrap=False,
        )

    assert result_names(first_tools) == [BandTool.NO_REPLY, BandTool.GET_PARTICIPANTS]
    assert peer.result_pairs(1) == [
        ("decline", '{"status": "no_reply"}'),
        ("read", "[]"),
    ]
    assert len(peer.requests) == 3
    assert next_tools.chat == ["new turn"]
    assert recorded_usage_payloads(first_tools)[0]["input_tokens"] == 10
    assert recorded_usage_payloads(next_tools)[0]["input_tokens"] == 20


@pytest.mark.asyncio
async def test_successful_reply_and_reads_keep_the_native_loop_running(
    sample_platform_message: PlatformMessage,
) -> None:
    peer = NativePeer(
        [
            response(
                tool(
                    BandTool.SEND_MESSAGE,
                    {"content": "progress", "mentions": ["user"]},
                    "reply",
                )
            ),
            response(tool(BandTool.GET_PARTICIPANTS, {}, "read")),
            response(),
        ]
    )
    tools = NativeTools()
    async with native_adapter(peer) as adapter:
        await run_turn(adapter, sample_platform_message, tools)

    assert len(peer.requests) == 3
    assert result_names(tools) == [BandTool.SEND_MESSAGE, BandTool.GET_PARTICIPANTS]
    assert reported_failures(tools) == []


@pytest.mark.asyncio
async def test_decline_keeps_effects_already_requested_in_the_same_batch(
    sample_platform_message: PlatformMessage,
) -> None:
    peer = NativePeer(
        [
            response(
                tool(BandTool.NO_REPLY, {}, "decline"),
                tool(
                    BandTool.SEND_MESSAGE,
                    {"content": "requested work", "mentions": ["user"]},
                    "reply",
                ),
            )
        ]
    )
    tools = NativeTools()
    async with native_adapter(peer) as adapter:
        await run_turn(adapter, sample_platform_message, tools)

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
    sample_platform_message: PlatformMessage,
) -> None:
    peer = NativePeer(
        [
            response(
                tool(BandTool.NO_REPLY, {}, "decline"),
                tool(BandTool.GET_PARTICIPANTS, {}, "read"),
            )
        ]
    )
    tools = BlockingReadTools()
    async with native_adapter(peer) as adapter:
        task = asyncio.create_task(run_turn(adapter, sample_platform_message, tools))
        try:
            await asyncio.wait_for(tools.read_started.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    assert tools.read_cancelled.is_set()
    assert len(peer.requests) == 1
    assert result_names(tools) == [BandTool.NO_REPLY]
    assert reported_failures(tools) == []
    assert recorded_usage_payloads(tools) == []
