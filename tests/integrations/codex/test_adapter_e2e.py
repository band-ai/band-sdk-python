"""Integration-level tests for CodexAdapter lifecycle behavior."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

import pytest

from thenvoi.adapters.codex import CodexAdapter, CodexAdapterConfig
from thenvoi.core.types import AgentInput, HistoryProvider, PlatformMessage
from thenvoi.integrations.codex import CodexJsonRpcError, RpcEvent
from thenvoi.testing import FakeAgentTools


def _platform_message(content: str, *, room_id: str = "room-1") -> PlatformMessage:
    return PlatformMessage(
        id=f"msg-{content[:8]}",
        room_id=room_id,
        content=content,
        sender_id="user-1",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


def _agent_input(
    content: str,
    tools: FakeAgentTools,
    *,
    raw_history: list[dict[str, Any]] | None = None,
    room_id: str = "room-1",
    is_session_bootstrap: bool = True,
) -> AgentInput:
    return AgentInput(
        msg=_platform_message(content, room_id=room_id),
        tools=tools,
        history=HistoryProvider(raw=raw_history or []),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=is_session_bootstrap,
        room_id=room_id,
    )


class _ToolSchemaFakeTools(FakeAgentTools):
    def get_openai_tool_schemas(
        self,
        *,
        include_memory: bool = False,
        include_contacts: bool = True,
    ) -> list[dict[str, Any]]:
        del include_memory, include_contacts
        return [
            {
                "type": "function",
                "function": {
                    "name": "thenvoi_send_message",
                    "description": "Send a message",
                    "parameters": {
                        "type": "object",
                        "properties": {"content": {"type": "string"}},
                    },
                },
            }
        ]


class _FakeCodexClient:
    def __init__(
        self,
        *,
        events: list[RpcEvent] | None = None,
        resume_error: Exception | None = None,
    ) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.responses: list[tuple[int | str, dict[str, Any]]] = []
        self._events = deque(events or [])
        self._resume_error = resume_error
        self._thread_counter = 0
        self._turn_counter = 0

    async def connect(self) -> None:
        return None

    async def initialize(
        self,
        *,
        client_name: str,
        client_title: str,
        client_version: str,
        experimental_api: bool = False,
        opt_out_notification_methods: list[str] | None = None,
    ) -> dict[str, Any]:
        return {"userAgent": f"{client_name}/{client_version}"}

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry_on_overload: bool = True,
    ) -> dict[str, Any]:
        payload = params or {}
        self.requests.append((method, payload))

        if method == "model/list":
            return {"data": [{"id": "gpt-5.3-codex", "hidden": False}]}
        if method == "thread/resume":
            if self._resume_error is not None:
                raise self._resume_error
            return {"thread": {"id": str(payload.get("threadId") or "thr-resumed")}}
        if method == "thread/start":
            self._thread_counter += 1
            return {"thread": {"id": f"thr-{self._thread_counter}"}}
        if method == "turn/start":
            self._turn_counter += 1
            return {
                "turn": {
                    "id": f"turn-{self._turn_counter}",
                    "status": "inProgress",
                    "items": [],
                    "error": None,
                }
            }
        return {}

    async def recv_event(self, timeout_s: float | None = None) -> RpcEvent:
        if not self._events:
            raise asyncio.TimeoutError()
        return self._events.popleft()

    async def respond(self, request_id: int | str, result: dict[str, Any]) -> None:
        self.responses.append((request_id, result))

    async def respond_error(
        self,
        request_id: int | str,
        *,
        code: int,
        message: str,
        data: Any | None = None,
    ) -> None:
        self.responses.append(
            (request_id, {"error": {"code": code, "message": message}})
        )

    async def close(self) -> None:
        return None


def _notify(method: str, params: dict[str, Any]) -> RpcEvent:
    return RpcEvent(
        kind="notification",
        method=method,
        params=params,
        id=None,
        raw={"method": method, "params": params},
    )


def _request(request_id: int, method: str, params: dict[str, Any]) -> RpcEvent:
    return RpcEvent(
        kind="request",
        method=method,
        params=params,
        id=request_id,
        raw={"id": request_id, "method": method, "params": params},
    )


@pytest.mark.asyncio
async def test_on_event_uses_converter_history_to_resume_thread() -> None:
    tools = _ToolSchemaFakeTools()
    fake_client = _FakeCodexClient(
        events=[
            _notify(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [],
                        "error": None,
                    }
                },
            )
        ]
    )
    adapter = CodexAdapter(
        config=CodexAdapterConfig(transport="ws"),
        client_factory=lambda _cfg: fake_client,
    )
    await adapter.on_started("Codex Agent", "Integration test agent")

    raw_history = [
        {
            "message_type": "task",
            "content": "historical mapping",
            "metadata": {
                "codex_thread_id": "thr-history",
                "codex_room_id": "room-1",
                "codex_created_at": datetime.now(timezone.utc).isoformat(),
            },
        }
    ]
    await adapter.on_event(
        _agent_input(
            "continue work",
            tools,
            raw_history=raw_history,
            room_id="room-1",
            is_session_bootstrap=True,
        )
    )

    methods = [method for method, _ in fake_client.requests]
    assert "thread/resume" in methods
    assert "thread/start" not in methods
    assert any("Status: resumed" in event["content"] for event in tools.events_sent)


@pytest.mark.asyncio
async def test_manual_approval_resolved_by_out_of_band_approve_command() -> None:
    tools = _ToolSchemaFakeTools()
    fake_client = _FakeCodexClient(
        events=[
            _request(
                7,
                "item/commandExecution/requestApproval",
                {"approvalId": "ap-1", "command": ["git", "push"]},
            ),
            _notify(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [],
                        "error": None,
                    }
                },
            ),
        ]
    )
    adapter = CodexAdapter(
        config=CodexAdapterConfig(
            transport="ws",
            approval_mode="manual",
            approval_wait_timeout_s=30.0,
        ),
        client_factory=lambda _cfg: fake_client,
    )
    await adapter.on_started("Codex Agent", "Integration test agent")

    first_turn = asyncio.create_task(
        adapter.on_event(
            _agent_input(
                "run protected command",
                tools,
                room_id="room-1",
                is_session_bootstrap=True,
            )
        )
    )

    start = time.monotonic()
    while time.monotonic() - start < 2.0:
        if any("Approval id: `ap-1`" in msg["content"] for msg in tools.messages_sent):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("Approval notification not sent within timeout")

    await adapter.on_event(
        _agent_input(
            "/approve ap-1",
            tools,
            room_id="room-1",
            is_session_bootstrap=False,
        )
    )
    await asyncio.wait_for(first_turn, timeout=2.0)

    assert (7, {"decision": "accept"}) in fake_client.responses
    assert any(
        "Approval `ap-1` resolved as `accept`." in msg["content"]
        for msg in tools.messages_sent
    )


@pytest.mark.asyncio
async def test_restart_rehydrates_mapping_from_previous_task_events() -> None:
    tools_first = _ToolSchemaFakeTools()
    fake_client_first = _FakeCodexClient(
        events=[
            _notify(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [],
                        "error": None,
                    }
                },
            )
        ]
    )
    adapter_first = CodexAdapter(
        config=CodexAdapterConfig(transport="ws"),
        client_factory=lambda _cfg: fake_client_first,
    )
    await adapter_first.on_started("Codex Agent", "Integration test agent")
    await adapter_first.on_event(
        _agent_input(
            "first turn",
            tools_first,
            room_id="room-9",
            is_session_bootstrap=True,
        )
    )

    persisted_history = list(tools_first.events_sent)

    tools_second = _ToolSchemaFakeTools()
    fake_client_second = _FakeCodexClient(
        events=[
            _notify(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [],
                        "error": None,
                    }
                },
            )
        ]
    )
    adapter_second = CodexAdapter(
        config=CodexAdapterConfig(transport="ws"),
        client_factory=lambda _cfg: fake_client_second,
    )
    await adapter_second.on_started("Codex Agent", "Integration test agent")
    await adapter_second.on_event(
        _agent_input(
            "second turn after restart",
            tools_second,
            raw_history=persisted_history,
            room_id="room-9",
            is_session_bootstrap=True,
        )
    )

    methods_second = [method for method, _ in fake_client_second.requests]
    assert "thread/resume" in methods_second
    assert "thread/start" not in methods_second


@pytest.mark.asyncio
async def test_resume_failure_injects_conversation_history() -> None:
    """Full lifecycle: first session produces history, second session resume fails,
    verify turn input contains history context."""
    # --- First session: produce some history ---
    tools_first = _ToolSchemaFakeTools()
    fake_client_first = _FakeCodexClient(
        events=[
            _notify(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [],
                        "error": None,
                    }
                },
            )
        ]
    )
    adapter_first = CodexAdapter(
        config=CodexAdapterConfig(transport="ws"),
        client_factory=lambda _cfg: fake_client_first,
    )
    await adapter_first.on_started("Codex Agent", "Integration test agent")
    await adapter_first.on_event(
        _agent_input(
            "refactor the auth module",
            tools_first,
            room_id="room-hist",
            is_session_bootstrap=True,
        )
    )

    # Simulate persisted history: task events + text messages
    persisted_history = list(tools_first.events_sent)
    persisted_history.append(
        {
            "message_type": "text",
            "content": "refactor the auth module",
            "sender_name": "Alice",
        }
    )
    persisted_history.append(
        {
            "message_type": "text",
            "content": "Done — split into auth_handler.py and middleware.py",
            "sender_name": "CodexAgent",
        }
    )

    # --- Second session: resume fails, should inject history ---
    tools_second = _ToolSchemaFakeTools()
    fake_client_second = _FakeCodexClient(
        events=[
            _notify(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [],
                        "error": None,
                    }
                },
            )
        ],
        resume_error=CodexJsonRpcError(code=-32002, message="Thread expired"),
    )
    adapter_second = CodexAdapter(
        config=CodexAdapterConfig(transport="ws"),
        client_factory=lambda _cfg: fake_client_second,
    )
    await adapter_second.on_started("Codex Agent", "Integration test agent")
    await adapter_second.on_event(
        _agent_input(
            "now add rate limiting",
            tools_second,
            raw_history=persisted_history,
            room_id="room-hist",
            is_session_bootstrap=True,
        )
    )

    methods = [method for method, _ in fake_client_second.requests]
    assert "thread/resume" in methods
    assert "thread/start" in methods

    turn_start = next(
        params
        for method, params in fake_client_second.requests
        if method == "turn/start"
    )
    turn_input = turn_start["input"]
    history_items = [
        item for item in turn_input if "[Conversation History]" in item["text"]
    ]
    assert len(history_items) == 1
    assert "[Alice]: refactor the auth module" in history_items[0]["text"]
    assert (
        "[CodexAgent]: Done — split into auth_handler.py and middleware.py"
        in history_items[0]["text"]
    )


@pytest.mark.asyncio
async def test_item_completed_forwards_internal_operations() -> None:
    """Full cycle: commandExecution + fileChange + agentMessage items all forwarded."""

    tools = _ToolSchemaFakeTools()
    fake_client = _FakeCodexClient(
        events=[
            _notify(
                "item/completed",
                {
                    "item": {
                        "type": "commandExecution",
                        "id": "cmd-1",
                        "command": "pytest tests/",
                        "cwd": "/workspace",
                        "aggregated_output": "5 passed",
                        "exitCode": 0,
                    }
                },
            ),
            _notify(
                "item/completed",
                {
                    "item": {
                        "type": "fileChange",
                        "id": "fc-1",
                        "changes": [{"path": "src/app.py"}],
                        "status": "applied",
                    }
                },
            ),
            _notify(
                "item/completed",
                {
                    "item": {
                        "type": "reasoning",
                        "id": "reason-1",
                        "summary": ["Tests pass, applying fix"],
                    }
                },
            ),
            _notify(
                "item/completed",
                {
                    "item": {
                        "type": "agentMessage",
                        "id": "msg-1",
                        "text": "Fixed the bug and all tests pass.",
                    }
                },
            ),
            _notify(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [],
                        "error": None,
                    }
                },
            ),
        ]
    )
    adapter = CodexAdapter(
        config=CodexAdapterConfig(
            transport="ws",
            enable_execution_reporting=True,
            emit_thought_events=True,
        ),
        client_factory=lambda _cfg: fake_client,
    )
    await adapter.on_started("Codex Agent", "Integration test agent")
    await adapter.on_event(
        _agent_input(
            "fix the failing test",
            tools,
            room_id="room-ops",
            is_session_bootstrap=True,
        )
    )

    # Verify tool events for commandExecution
    tool_call_events = [
        e for e in tools.events_sent if e["message_type"] == "tool_call"
    ]
    tool_result_events = [
        e for e in tools.events_sent if e["message_type"] == "tool_result"
    ]
    # commandExecution + fileChange = 2 tool_call + 2 tool_result
    assert len(tool_call_events) == 2
    assert len(tool_result_events) == 2

    exec_call = json.loads(tool_call_events[0]["content"])
    assert exec_call["name"] == "exec"
    assert exec_call["args"]["command"] == "pytest tests/"

    file_call = json.loads(tool_call_events[1]["content"])
    assert file_call["name"] == "file_edit"

    # Verify thought event for reasoning
    thought_events = [e for e in tools.events_sent if e["message_type"] == "thought"]
    assert len(thought_events) == 1
    assert "Tests pass" in thought_events[0]["content"]

    # Verify final text message from agentMessage
    assert any(
        msg["content"] == "Fixed the bug and all tests pass."
        for msg in tools.messages_sent
    )
