"""Tests for codex websocket JSON-RPC client."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from band.integrations.codex import (
    CodexJsonRpcError,
    CodexWebSocketClient,
    OverloadRetryPolicy,
)


class FakeCodexWebSocket:
    """In-memory websocket transport for JSON-RPC behavior tests."""

    def __init__(self, scenario: str):
        self.scenario = scenario
        self._incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.initialized = False
        self.sent_overload_once = False
        self.tool_response: dict[str, Any] | None = None

    async def send(self, text: str) -> None:
        message = json.loads(text)
        method = message.get("method")

        if method == "initialize":
            await self._emit(
                {"id": message["id"], "result": {"server": "fake_codex_ws"}}
            )
            return

        if method == "initialized":
            self.initialized = True
            return

        if method == "thread/start":
            if not self.initialized:
                await self._emit(
                    {
                        "id": message["id"],
                        "error": {"code": -32002, "message": "Not initialized"},
                    }
                )
                return

            if self.scenario in {"retry_once", "always_overload"}:
                if self.scenario == "always_overload" or not self.sent_overload_once:
                    self.sent_overload_once = True
                    await self._emit(
                        {
                            "id": message["id"],
                            "error": {
                                "code": -32001,
                                "message": "Server overloaded; retry later.",
                            },
                        }
                    )
                    return

            await self._emit(
                {"id": message["id"], "result": {"thread": {"id": "thr_ws"}}}
            )
            await self._emit(
                {"method": "thread/started", "params": {"thread": {"id": "thr_ws"}}}
            )
            return

        if method == "turn/start":
            await self._emit(
                {
                    "id": message["id"],
                    "result": {
                        "turn": {
                            "id": "turn_ws",
                            "status": "inProgress",
                            "items": [],
                            "error": None,
                        }
                    },
                }
            )
            await self._emit(
                {
                    "method": "turn/started",
                    "params": {
                        "turn": {
                            "id": "turn_ws",
                            "status": "inProgress",
                            "items": [],
                            "error": None,
                        }
                    },
                }
            )

            if self.scenario == "server_request":
                await self._emit(
                    {
                        "id": "srv-tool-1",
                        "method": "item/tool/call",
                        "params": {
                            "threadId": "thr_ws",
                            "turnId": "turn_ws",
                            "callId": "call_1",
                            "tool": "echo",
                            "arguments": {"value": "ping"},
                        },
                    }
                )
                return

            await self._emit_turn_success()
            return

        # Client response to server request
        if message.get("id") is not None and (
            message.get("result") is not None or message.get("error") is not None
        ):
            if self.scenario == "server_request" and str(message["id"]) == "srv-tool-1":
                self.tool_response = message
                result = message.get("result") or {}
                status = "completed" if result.get("success") else "failed"
                await self._emit(
                    {
                        "method": "item/completed",
                        "params": {
                            "item": {
                                "type": "mcpToolCall",
                                "id": "tool_1",
                                "status": status,
                            }
                        },
                    }
                )
                await self._emit_turn_success()
            return

        # Unknown method fallback
        if message.get("id") is not None:
            await self._emit(
                {
                    "id": message["id"],
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}",
                    },
                }
            )

    async def _emit_turn_success(self) -> None:
        await self._emit(
            {
                "method": "item/agentMessage/delta",
                "params": {"itemId": "msg_1", "delta": "harness-ok"},
            }
        )
        await self._emit(
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "type": "agentMessage",
                        "id": "msg_1",
                        "text": "harness-ok",
                    }
                },
            }
        )
        await self._emit(
            {
                "method": "turn/completed",
                "params": {
                    "turn": {
                        "id": "turn_ws",
                        "status": "completed",
                        "items": [],
                        "error": None,
                    }
                },
            }
        )

    async def _emit(self, payload: dict[str, Any]) -> None:
        await self._incoming.put(json.dumps(payload))

    async def close(self) -> None:
        await self._incoming.put(None)

    def __aiter__(self) -> FakeCodexWebSocket:
        return self

    async def __anext__(self) -> str:
        value = await self._incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value


async def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    scenario: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    retry_policy: OverloadRetryPolicy | None = None,
) -> tuple[CodexWebSocketClient, FakeCodexWebSocket]:
    fake_ws = FakeCodexWebSocket(scenario=scenario)

    async def fake_connect(*_args, **_kwargs):
        return fake_ws

    monkeypatch.setattr("websockets.asyncio.client.connect", fake_connect)

    client = CodexWebSocketClient(
        ws_url="ws://fake.local",
        sleep=sleep,
        retry_policy=retry_policy,
    )
    await client.connect()
    await client.initialize(
        client_name="test_ws_client",
        client_title="Test WS Client",
        client_version="0.1.0",
        experimental_api=True,
    )
    return client, fake_ws


@pytest.mark.asyncio
async def test_websocket_client_connect_sets_large_max_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, Any] = {}
    fake_ws = FakeCodexWebSocket(scenario="basic")

    async def fake_connect(*_args, **kwargs):
        captured_kwargs.update(kwargs)
        return fake_ws

    monkeypatch.setattr("websockets.asyncio.client.connect", fake_connect)

    client = CodexWebSocketClient(ws_url="ws://fake.local")
    await client.connect()
    try:
        assert captured_kwargs.get("max_size") == 16 * 1024 * 1024
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_websocket_client_basic_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _fake_ws = await _build_client(monkeypatch, scenario="basic")
    try:
        thread_result = await client.request("thread/start", {"cwd": "."})
        assert thread_result["thread"]["id"] == "thr_ws"

        turn_result = await client.request(
            "turn/start",
            {
                "threadId": "thr_ws",
                "input": [{"type": "text", "text": "ping"}],
            },
        )
        assert turn_result["turn"]["id"] == "turn_ws"

        methods: list[str] = []
        deltas = ""
        while True:
            event = await client.recv_event(timeout_s=1.0)
            methods.append(event.method)
            if event.method == "item/agentMessage/delta":
                params = event.params if isinstance(event.params, dict) else {}
                deltas += str(params.get("delta", ""))
            if event.method == "turn/completed":
                break

        assert "thread/started" in methods
        assert "turn/started" in methods
        assert deltas == "harness-ok"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_websocket_client_routes_server_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, fake_ws = await _build_client(monkeypatch, scenario="server_request")
    try:
        await client.request("thread/start", {"cwd": "."})
        await client.request(
            "turn/start",
            {
                "threadId": "thr_ws",
                "input": [{"type": "text", "text": "use tool"}],
            },
        )

        saw_tool_request = False
        while True:
            event = await client.recv_event(timeout_s=1.0)
            if event.kind == "request":
                assert event.method == "item/tool/call"
                saw_tool_request = True
                await client.respond(
                    event.id,
                    {
                        "contentItems": [{"type": "inputText", "text": "pong"}],
                        "success": True,
                    },
                )
                continue
            if event.method == "turn/completed":
                break

        assert saw_tool_request is True
        assert (fake_ws.tool_response or {}).get("result", {}).get("success") is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_websocket_client_retries_overload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    retry_policy = OverloadRetryPolicy(
        max_attempts=3,
        base_delay_s=0.01,
        max_delay_s=0.05,
        jitter_s=0.0,
    )
    client, _fake_ws = await _build_client(
        monkeypatch,
        scenario="retry_once",
        sleep=fake_sleep,
        retry_policy=retry_policy,
    )
    try:
        thread_result = await client.request("thread/start", {"cwd": "."})
        assert thread_result["thread"]["id"] == "thr_ws"
        assert delays == [0.01]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_websocket_client_server_close_fails_pending_and_enqueues_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the server closes the connection, pending futures fail and a disconnect event is enqueued."""

    class CloseAfterInitWebSocket(FakeCodexWebSocket):
        """Accepts initialization but closes on the next request."""

        async def send(self, text: str) -> None:
            message = json.loads(text)
            method = message.get("method")

            if method == "initialize":
                await self._emit(
                    {"id": message["id"], "result": {"server": "fake_codex_ws"}}
                )
                return
            if method == "initialized":
                self.initialized = True
                return

            # Close the websocket for any subsequent request
            await self._incoming.put(None)

    fake_ws = CloseAfterInitWebSocket(scenario="close_after_init")

    async def fake_connect(*_args: Any, **_kwargs: Any) -> CloseAfterInitWebSocket:
        return fake_ws

    monkeypatch.setattr("websockets.asyncio.client.connect", fake_connect)

    client = CodexWebSocketClient(ws_url="ws://fake.local")
    await client.connect()
    await client.initialize(
        client_name="test_ws_client",
        client_title="Test WS Client",
        client_version="0.1.0",
    )

    # Send a request — the server will close instead of responding
    with pytest.raises(RuntimeError, match="disconnected"):
        await client.request("thread/start", {"cwd": "/tmp"})

    # A transport/closed event should be available
    event = await client.recv_event(timeout_s=1.0)
    assert event.method == "transport/closed"
    await client.close()


@pytest.mark.asyncio
async def test_websocket_client_raises_after_retry_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    retry_policy = OverloadRetryPolicy(
        max_attempts=2,
        base_delay_s=0.01,
        max_delay_s=0.05,
        jitter_s=0.0,
    )
    client, _fake_ws = await _build_client(
        monkeypatch,
        scenario="always_overload",
        sleep=fake_sleep,
        retry_policy=retry_policy,
    )
    try:
        with pytest.raises(CodexJsonRpcError) as exc_info:
            await client.request("thread/start", {"cwd": "."})

        assert exc_info.value.code == -32001
        assert delays == [0.01]
    finally:
        await client.close()
