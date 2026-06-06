"""Tests for the openai-codex backed bridge client."""

from __future__ import annotations

import asyncio
import queue
import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest

from band.integrations.codex.sdk_client import CodexSdkClient


class FakeModel:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        return self.data


@dataclass
class FakeNotification:
    method: str
    payload: FakeModel


@dataclass
class FakeCodexConfig:
    launch_args_override: tuple[str, ...] | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None
    client_name: str = "codex_python_sdk"
    client_title: str = "Codex Python SDK"
    client_version: str = "0.0.0"
    experimental_api: bool = True


class FakeCodexClient:
    last_instance: "FakeCodexClient | None" = None

    def __init__(self, *, config: FakeCodexConfig, approval_handler: Any) -> None:
        self.config = config
        self.approval_handler = approval_handler
        self.started = False
        self.closed = False
        self.thread_start_params: dict[str, Any] | None = None
        self.turn_start_params: dict[str, Any] | None = None
        self.turn_notifications: list[FakeNotification | BaseException] = []
        self.unregistered_turns: list[str] = []
        FakeCodexClient.last_instance = self

    def start(self) -> None:
        self.started = True

    def initialize(self) -> FakeModel:
        return FakeModel({"userAgent": "fake-codex"})

    def close(self) -> None:
        self.closed = True

    def model_list(self, *, include_hidden: bool = False) -> FakeModel:
        return FakeModel(
            {
                "data": [
                    {"id": "visible-model", "hidden": False},
                    {"id": "hidden-model", "hidden": include_hidden is False},
                ]
            }
        )

    def thread_start(self, params: dict[str, Any]) -> FakeModel:
        self.thread_start_params = params
        return FakeModel({"thread": {"id": "thread-1"}})

    def thread_resume(self, thread_id: str, params: dict[str, Any]) -> FakeModel:
        return FakeModel({"thread": {"id": thread_id, "params": params}})

    def turn_start(
        self,
        thread_id: str,
        input_items: list[dict[str, Any]],
        *,
        params: dict[str, Any],
    ) -> FakeModel:
        self.turn_start_params = {
            "thread_id": thread_id,
            "input_items": input_items,
            "params": params,
        }
        return FakeModel({"turn": {"id": "turn-1", "status": "inProgress"}})

    def next_turn_notification(self, turn_id: str) -> FakeNotification:
        if not self.turn_notifications:
            raise RuntimeError(f"no notification for {turn_id}")
        item = self.turn_notifications.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def unregister_turn_notifications(self, turn_id: str) -> None:
        self.unregistered_turns.append(turn_id)

    def turn_interrupt(self, thread_id: str, turn_id: str) -> FakeModel:
        return FakeModel({"threadId": thread_id, "turnId": turn_id})


@pytest.fixture
def fake_openai_codex(monkeypatch: pytest.MonkeyPatch) -> type[FakeCodexClient]:
    FakeCodexClient.last_instance = None

    package = types.ModuleType("openai_codex")
    client_module = types.ModuleType("openai_codex.client")
    retry_module = types.ModuleType("openai_codex.retry")

    client_module.CodexClient = FakeCodexClient
    client_module.CodexConfig = FakeCodexConfig

    def retry_on_overload(op: Any, **kwargs: Any) -> Any:
        return op()

    retry_module.retry_on_overload = retry_on_overload

    monkeypatch.setitem(sys.modules, "openai_codex", package)
    monkeypatch.setitem(sys.modules, "openai_codex.client", client_module)
    monkeypatch.setitem(sys.modules, "openai_codex.retry", retry_module)
    return FakeCodexClient


@pytest.mark.asyncio
async def test_thread_start_passes_dynamic_tools_to_openai_sdk_client(
    fake_openai_codex: type[FakeCodexClient],
) -> None:
    client = CodexSdkClient(cwd="/repo", client_name="band-test")

    await client.connect()
    init = await client.initialize(
        client_name="ignored",
        client_title="ignored",
        client_version="ignored",
        experimental_api=True,
    )
    result = await client.request(
        "thread/start",
        {
            "model": "gpt-5.5",
            "cwd": "/repo",
            "dynamicTools": [
                {
                    "name": "band_send_message",
                    "description": "Send a message",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ],
        },
    )

    sdk = fake_openai_codex.last_instance
    assert sdk is not None
    assert sdk.started is True
    assert sdk.config.cwd == "/repo"
    assert sdk.config.client_name == "band-test"
    assert init == {"userAgent": "fake-codex"}
    assert result == {"thread": {"id": "thread-1"}}
    assert sdk.thread_start_params is not None
    assert sdk.thread_start_params["dynamicTools"][0]["name"] == "band_send_message"

    await client.close()


@pytest.mark.asyncio
async def test_turn_start_pumps_notifications_into_async_event_queue(
    fake_openai_codex: type[FakeCodexClient],
) -> None:
    client = CodexSdkClient()
    await client.connect()
    await client.initialize(
        client_name="ignored",
        client_title="ignored",
        client_version="ignored",
        experimental_api=True,
    )
    sdk = fake_openai_codex.last_instance
    assert sdk is not None
    sdk.turn_notifications = [
        FakeNotification(
            "item/completed",
            FakeModel({"item": {"type": "agentMessage", "text": "hello"}}),
        ),
        FakeNotification(
            "turn/completed",
            FakeModel({"turn": {"id": "turn-1", "status": "completed"}}),
        ),
    ]

    result = await client.request(
        "turn/start",
        {"threadId": "thread-1", "input": [{"type": "text", "text": "hi"}]},
    )
    first_event = await client.recv_event(timeout_s=1)
    second_event = await client.recv_event(timeout_s=1)

    assert result["turn"]["id"] == "turn-1"
    assert sdk.turn_start_params is not None
    assert sdk.turn_start_params["thread_id"] == "thread-1"
    assert first_event.method == "item/completed"
    assert first_event.params == {"item": {"type": "agentMessage", "text": "hello"}}
    assert second_event.method == "turn/completed"
    assert second_event.params == {"turn": {"id": "turn-1", "status": "completed"}}

    await client.close()


@pytest.mark.asyncio
async def test_server_request_handler_bridges_to_respond(
    fake_openai_codex: type[FakeCodexClient],
) -> None:
    client = CodexSdkClient()
    await client.connect()
    sdk = fake_openai_codex.last_instance
    assert sdk is not None

    handler_task = asyncio.create_task(
        asyncio.to_thread(
            sdk.approval_handler,
            "item/tool/call",
            {"tool": "band_lookup_peers", "arguments": {"page": 1}},
        )
    )
    event = await client.recv_event(timeout_s=1)

    assert event.kind == "request"
    assert event.method == "item/tool/call"
    assert event.params == {"tool": "band_lookup_peers", "arguments": {"page": 1}}
    assert event.id is not None

    await client.respond(
        event.id,
        {
            "contentItems": [{"type": "inputText", "text": "peers"}],
            "success": True,
        },
    )

    assert await asyncio.wait_for(handler_task, timeout=1) == {
        "contentItems": [{"type": "inputText", "text": "peers"}],
        "success": True,
    }

    await client.close()


@pytest.mark.asyncio
async def test_dropped_server_request_returns_failure_instead_of_hanging() -> None:
    client = CodexSdkClient()
    client._events = queue.Queue(maxsize=1)  # type: ignore[assignment]

    first_task = asyncio.create_task(
        asyncio.to_thread(
            client._handle_server_request,
            "item/tool/call",
            {"tool": "first"},
        )
    )
    for _ in range(100):
        if not client._events.empty():
            break
        await asyncio.sleep(0)
    assert client._events.qsize() == 1

    second_task = asyncio.create_task(
        asyncio.to_thread(
            client._handle_server_request,
            "item/tool/call",
            {"tool": "second"},
        )
    )

    first_response = await asyncio.wait_for(first_task, timeout=1)
    assert first_response["success"] is False
    assert "dropped" in first_response["contentItems"][0]["text"]

    second_event = await client.recv_event(timeout_s=1)
    assert second_event.kind == "request"
    assert second_event.params == {"tool": "second"}
    assert second_event.id is not None

    await client.respond(
        second_event.id,
        {
            "contentItems": [{"type": "inputText", "text": "ok"}],
            "success": True,
        },
    )
    assert await asyncio.wait_for(second_task, timeout=1) == {
        "contentItems": [{"type": "inputText", "text": "ok"}],
        "success": True,
    }
