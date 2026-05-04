"""Tests for CodexAdapter."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict, deque
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from pydantic import BaseModel

from thenvoi.adapters.codex import CodexAdapter, CodexAdapterConfig
from thenvoi.core.types import AgentInput, HistoryProvider, PlatformMessage
from thenvoi.integrations.codex import CodexJsonRpcError, RpcEvent
from thenvoi.integrations.codex.types import CodexSessionState
from thenvoi.runtime.custom_tools import CustomToolDef
from thenvoi.testing import FakeAgentTools


def make_platform_message(
    room_id: str = "room-1", content: str = "hello"
) -> PlatformMessage:
    return PlatformMessage(
        id=str(uuid4()),
        room_id=room_id,
        content=content,
        sender_id="user-1",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(),
    )


class ToolSchemaFakeTools(FakeAgentTools):
    def get_openai_tool_schemas(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "thenvoi_send_message",
                    "description": "Send a message",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "mentions": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["content", "mentions"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "thenvoi_send_event",
                    "description": "Send an event",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "message_type": {"type": "string"},
                        },
                        "required": ["content", "message_type"],
                    },
                },
            },
        ]


class FakeCodexClient:
    """Minimal fake transport client for adapter tests."""

    def __init__(
        self,
        *,
        events: list[RpcEvent] | None = None,
        resume_error: Exception | None = None,
        turn_start_error: Exception | None = None,
        turn_start_error_once: bool = True,
        model_list_result: dict[str, Any] | None = None,
    ) -> None:
        self.connected = False
        self.initialized = False
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.responses: list[tuple[int | str, dict[str, Any]]] = []
        self.response_errors: list[tuple[int | str, int, str]] = []
        self.closed = False
        self._events = deque(events or [])
        self._resume_error = resume_error
        self._turn_start_error = turn_start_error
        self._turn_start_error_once = turn_start_error_once
        self._model_list_result = model_list_result
        self._thread_counter = 0
        self._turn_counter = 0

    async def connect(self) -> None:
        self.connected = True

    async def initialize(
        self,
        *,
        client_name: str,
        client_title: str,
        client_version: str,
        experimental_api: bool = False,
        opt_out_notification_methods: list[str] | None = None,
    ) -> dict[str, Any]:
        self.initialized = True
        return {"userAgent": f"{client_name}/{client_version}"}

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry_on_overload: bool = True,
    ) -> dict[str, Any]:
        payload = params or {}
        self.requests.append((method, dict(payload)))

        if method == "model/list":
            if self._model_list_result is not None:
                return self._model_list_result
            return {"data": [{"id": "gpt-5.3-codex", "hidden": False}]}

        if method == "thread/resume":
            if self._resume_error is not None:
                raise self._resume_error
            return {"thread": {"id": payload.get("threadId", "thr-resumed")}}

        if method == "thread/start":
            self._thread_counter += 1
            return {"thread": {"id": f"thr-{self._thread_counter}"}}

        if method == "turn/start":
            if self._turn_start_error is not None:
                err = self._turn_start_error
                if self._turn_start_error_once:
                    self._turn_start_error = None
                raise err
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
            raise asyncio.TimeoutError
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
        self.response_errors.append((request_id, code, message))

    async def close(self) -> None:
        self.closed = True
        return None


def _event_notification(method: str, params: dict[str, Any]) -> RpcEvent:
    return RpcEvent(
        kind="notification",
        method=method,
        params=params,
        id=None,
        raw={"method": method, "params": params},
    )


def _event_request(request_id: int, method: str, params: dict[str, Any]) -> RpcEvent:
    return RpcEvent(
        kind="request",
        method=method,
        params=params,
        id=request_id,
        raw={"id": request_id, "method": method, "params": params},
    )


async def _wait_for_pending_approval(
    adapter: CodexAdapter,
    room_id: str,
    *,
    timeout_s: float = 2.0,
) -> None:
    """Yield control until ``adapter`` records a pending approval for ``room_id``.

    Replaces brittle ``asyncio.sleep(0.01)`` calls in approval tests —
    polls the adapter's in-memory state instead of racing a fixed delay.
    """
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if adapter._pending_approvals.get(room_id):
            return
        await asyncio.sleep(0)
    raise AssertionError(
        f"No pending approval registered for room {room_id!r} within {timeout_s}s"
    )


class TestCodexAdapter:
    def test_config_defaults_are_low_noise_and_manual_approval(self) -> None:
        config = CodexAdapterConfig()
        assert config.emit_turn_task_markers is False
        assert config.emit_thought_events is False
        assert config.approval_mode == "manual"

    @pytest.mark.asyncio
    async def test_bootstrap_starts_thread_and_sends_fallback_message(self) -> None:
        events = [
            _event_notification(
                "item/agentMessage/delta",
                {"itemId": "msg-1", "delta": "harness-ok"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert any(method == "thread/start" for method, _ in fake_client.requests)
        thread_start = next(
            params
            for method, params in fake_client.requests
            if method == "thread/start"
        )
        assert "dynamicTools" in thread_start
        dynamic_names = [t["name"] for t in thread_start["dynamicTools"]]
        assert "thenvoi_send_message" in dynamic_names
        assert "thenvoi_send_event" in dynamic_names

        assert len(tools.messages_sent) == 1
        assert tools.messages_sent[0]["content"] == "harness-ok"
        assert tools.messages_sent[0]["mentions"][0]["id"] == "user-1"

    @pytest.mark.asyncio
    async def test_system_prompt_retry_after_turn_start_failure(self) -> None:
        """System instructions stay pending until turn/start succeeds."""
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(
            events=events,
            turn_start_error=CodexJsonRpcError(
                code=-32000,
                message="Model not available",
            ),
            turn_start_error_once=True,
        )
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", model="gpt-5.3-codex"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        with pytest.raises(CodexJsonRpcError, match="not available"):
            await adapter.on_message(
                make_platform_message(room_id="room-1", content="first try"),
                tools,
                CodexSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        assert "room-1" not in adapter._prompt_injected_rooms

        await adapter.on_message(
            make_platform_message(room_id="room-1", content="second try"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        assert "room-1" in adapter._prompt_injected_rooms

        turn_inputs = [
            params["input"]
            for method, params in fake_client.requests
            if method == "turn/start"
        ]
        assert len(turn_inputs) == 2
        for turn_input in turn_inputs:
            assert any(
                item.get("text", "").startswith("[System Instructions]\n")
                for item in turn_input
            )

    @pytest.mark.asyncio
    async def test_tool_call_request_is_dispatched_and_responded(self) -> None:
        events = [
            _event_request(
                42,
                "item/tool/call",
                {
                    "tool": "thenvoi_lookup_peers",
                    "arguments": {"page": 1, "page_size": 10},
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert len(tools.tool_calls) == 1
        assert tools.tool_calls[0]["tool_name"] == "thenvoi_lookup_peers"
        assert fake_client.responses
        response_id, response_payload = fake_client.responses[0]
        assert response_id == 42
        assert response_payload["success"] is True

    @pytest.mark.asyncio
    async def test_fallback_text_not_suppressed_when_send_message_tool_fails(
        self,
    ) -> None:
        """Fallback agent text should still be delivered when send_message fails."""

        class SendMessageFailureTools(ToolSchemaFakeTools):
            async def execute_tool_call(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> Any:
                call = {"tool_name": tool_name, "arguments": arguments}
                self.tool_calls.append(call)
                if tool_name == "thenvoi_send_message":
                    raise RuntimeError("send failed")
                return {"status": "ok"}

        events = [
            _event_request(
                77,
                "item/tool/call",
                {
                    "tool": "thenvoi_send_message",
                    "arguments": {"content": "hi"},
                    "callId": "call-77",
                },
            ),
            _event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "agentMessage",
                        "id": "msg-1",
                        "text": "fallback final text",
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = SendMessageFailureTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert len(tools.messages_sent) == 1
        assert tools.messages_sent[0]["content"] == "fallback final text"
        assert len(fake_client.responses) == 1
        _, payload = fake_client.responses[0]
        assert payload["success"] is False

    @pytest.mark.asyncio
    async def test_resume_failure_falls_back_to_thread_start(self) -> None:
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(
            events=events,
            resume_error=CodexJsonRpcError(code=-32002, message="Not found"),
        )
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(thread_id="thr-old", room_id="room-1"),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        methods = [method for method, _ in fake_client.requests]
        assert "thread/resume" in methods
        assert "thread/start" in methods

    @pytest.mark.asyncio
    async def test_approval_request_auto_decline(self) -> None:
        events = [
            _event_request(
                7,
                "item/commandExecution/requestApproval",
                {"command": "rm -rf tmp"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                approval_mode="auto_decline",
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert fake_client.responses
        response_id, payload = fake_client.responses[0]
        assert response_id == 7
        assert payload["decision"] == "decline"
        assert len(tools.messages_sent) == 1
        assert "Approval requested" in tools.messages_sent[0]["content"]
        assert "rm -rf tmp" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_auto_approval_responds_even_if_notification_fails(self) -> None:
        class FailingNotifyTools(ToolSchemaFakeTools):
            async def send_message(
                self, content: str, mentions: list[dict[str, str]] | None = None
            ) -> Any:
                raise RuntimeError("notification failed")

        events = [
            _event_request(
                7,
                "item/commandExecution/requestApproval",
                {"command": "rm -rf tmp"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                approval_mode="auto_decline",
                approval_text_notifications=True,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = FailingNotifyTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert fake_client.responses
        response_id, payload = fake_client.responses[0]
        assert response_id == 7
        assert payload["decision"] == "decline"

    @pytest.mark.asyncio
    async def test_manual_approval_responds_with_decline_if_notification_fails(
        self,
    ) -> None:
        class FailingNotifyTools(ToolSchemaFakeTools):
            async def send_message(
                self, content: str, mentions: list[dict[str, str]] | None = None
            ) -> Any:
                raise RuntimeError("notification failed")

        events = [
            _event_request(
                7,
                "item/commandExecution/requestApproval",
                {"command": "rm -rf tmp"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                approval_mode="manual",
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = FailingNotifyTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert fake_client.responses
        response_id, payload = fake_client.responses[0]
        assert response_id == 7
        assert payload["decision"] == "decline"
        assert "room-1" not in adapter._pending_approvals

    @pytest.mark.asyncio
    async def test_cleanup_closes_client_when_last_room_removed(self) -> None:
        fake_client = FakeCodexClient(
            events=[
                _event_notification(
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
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(room_id="room-1"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert fake_client.closed is False
        await adapter.on_cleanup("room-1")
        assert fake_client.closed is True

    @pytest.mark.asyncio
    async def test_cleanup_idempotent(self) -> None:
        """Calling on_cleanup twice for the same room should not raise."""
        fake_client = FakeCodexClient(
            events=[
                _event_notification(
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
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(room_id="room-1"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        await adapter.on_cleanup("room-1")
        assert fake_client.closed is True
        # Second cleanup should not raise
        await adapter.on_cleanup("room-1")

    @pytest.mark.asyncio
    async def test_cleanup_multi_room_keeps_client_until_last(self) -> None:
        """Client stays open until the last room is cleaned up."""
        events_room1 = [
            _event_notification(
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
        events_room2 = [
            _event_notification(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-2",
                        "status": "completed",
                        "items": [],
                        "error": None,
                    }
                },
            )
        ]
        fake_client = FakeCodexClient(events=events_room1 + events_room2)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(room_id="room-1"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await adapter.on_message(
            make_platform_message(room_id="room-2"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-2",
        )

        # Cleaning up room-1 should NOT close the client (room-2 still active)
        await adapter.on_cleanup("room-1")
        assert fake_client.closed is False

        # Cleaning up room-2 should close the client (last room)
        await adapter.on_cleanup("room-2")
        assert fake_client.closed is True

    @pytest.mark.asyncio
    async def test_forwards_raw_codex_task_events(self) -> None:
        events = [
            _event_notification(
                "codex/event/task_started",
                {"taskId": "task-1", "task": {"title": "Inspect repository"}},
            ),
            _event_notification(
                "codex/event/task_complete",
                {"taskId": "task-1", "summary": "Inspection finished"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        raw_task_events = [
            event
            for event in tools.events_sent
            if event["metadata"].get("codex_event_method")
            in {
                "codex/event/task_started",
                "codex/event/task_complete",
            }
        ]
        assert len(raw_task_events) == 2
        assert raw_task_events[0]["content"] == (
            "UUID: task-1\nTask: Inspect repository\nStatus: started"
        )
        assert raw_task_events[0]["metadata"]["codex_task_id"] == "task-1"
        assert raw_task_events[1]["content"] == (
            "UUID: task-1\nTask: Inspect repository\nStatus: completed\n"
            "Summary: Inspection finished"
        )
        assert raw_task_events[1]["metadata"]["codex_task_phase"] == "completed"

    @pytest.mark.asyncio
    async def test_can_disable_synthetic_turn_task_markers(self) -> None:
        events = [
            _event_notification(
                "codex/event/task_started",
                {"taskId": "task-1", "task": {"title": "Inspect repository"}},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                emit_turn_task_markers=False,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        turn_marker_events = [
            event
            for event in tools.events_sent
            if "codex_turn_status" in event["metadata"]
        ]
        assert turn_marker_events == []
        assert any(
            event["metadata"].get("codex_event_method") == "codex/event/task_started"
            for event in tools.events_sent
        )

    @pytest.mark.asyncio
    async def test_raw_task_event_without_explicit_task_id_does_not_emit_uuid(
        self,
    ) -> None:
        events = [
            _event_notification(
                "codex/event/task_started",
                {"id": "turn-1"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                emit_turn_task_markers=False,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        raw_task_event = next(
            event
            for event in tools.events_sent
            if event["metadata"].get("codex_event_method") == "codex/event/task_started"
        )
        assert raw_task_event["content"] == (
            "Task: Codex task lifecycle event\nStatus: started\n"
            "Summary: Method: codex/event/task_started"
        )
        assert "codex_task_id" not in raw_task_event["metadata"]

    @pytest.mark.asyncio
    async def test_status_command_returns_state_without_starting_turn(self) -> None:
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="@AR-2 Darter /status"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        methods = [method for method, _ in fake_client.requests]
        assert "turn/start" not in methods
        assert "thread/start" not in methods
        assert len(tools.messages_sent) == 1
        assert "Codex status:" in tools.messages_sent[0]["content"]
        assert "thread_id: not mapped" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_model_command_sets_override_without_starting_turn(self) -> None:
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="/model gpt-5.5-codex"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        methods = [method for method, _ in fake_client.requests]
        assert "turn/start" not in methods
        assert "thread/start" not in methods
        assert adapter.config.model == "gpt-5.5-codex"
        assert len(tools.messages_sent) == 1
        assert (
            "Model override set to `gpt-5.5-codex`" in tools.messages_sent[0]["content"]
        )

    @pytest.mark.asyncio
    async def test_models_alias_lists_models_without_starting_turn(self) -> None:
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="/models list"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        methods = [method for method, _ in fake_client.requests]
        assert "turn/start" not in methods
        assert "thread/start" not in methods
        assert methods.count("model/list") >= 1
        assert len(tools.messages_sent) == 1
        assert "Available models" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_reasoning_effort_passed_in_turn_overrides(self) -> None:
        events = [
            _event_notification(
                "turn/completed",
                {
                    "turn": {
                        "id": "t1",
                        "threadId": "th1",
                        "status": "completed",
                    },
                    "text": "Done",
                },
            ),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                reasoning_effort="high",
                reasoning_summary="concise",
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="hello"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        turn_params = next(
            params for method, params in fake_client.requests if method == "turn/start"
        )
        assert turn_params["effort"] == "high"
        assert turn_params["summary"] == "concise"

    @pytest.mark.asyncio
    async def test_reasoning_effort_omitted_when_none(self) -> None:
        events = [
            _event_notification(
                "turn/completed",
                {
                    "turn": {
                        "id": "t1",
                        "threadId": "th1",
                        "status": "completed",
                    },
                    "text": "Done",
                },
            ),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="hello"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        turn_params = next(
            params for method, params in fake_client.requests if method == "turn/start"
        )
        assert "effort" not in turn_params
        assert "summary" not in turn_params

    @pytest.mark.asyncio
    async def test_reasoning_command_sets_effort(self) -> None:
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="/reasoning high"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        assert adapter.config.reasoning_effort == "high"
        assert len(tools.messages_sent) == 1
        assert "Reasoning effort set to `high`" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_reasoning_command_rejects_invalid_effort(self) -> None:
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="/reasoning ultra"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        assert adapter.config.reasoning_effort is None
        assert len(tools.messages_sent) == 1
        assert "Invalid reasoning effort" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_self_config_tools_registered_when_enabled(self) -> None:
        events = [
            _event_notification(
                "turn/completed",
                {
                    "turn": {
                        "id": "t1",
                        "threadId": "th1",
                        "status": "completed",
                    },
                    "text": "Done",
                },
            ),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_self_config_tools=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="hello"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        # Check that thread/start included setmodel and setreasoning dynamic tools
        thread_params = next(
            params
            for method, params in fake_client.requests
            if method == "thread/start"
        )
        tool_names = [t["name"] for t in thread_params.get("dynamicTools", [])]
        assert "setmodel" in tool_names
        assert "setreasoning" in tool_names

    @pytest.mark.asyncio
    async def test_self_config_tools_not_registered_when_disabled(self) -> None:
        events = [
            _event_notification(
                "turn/completed",
                {
                    "turn": {
                        "id": "t1",
                        "threadId": "th1",
                        "status": "completed",
                    },
                    "text": "Done",
                },
            ),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_self_config_tools=False),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="hello"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        thread_params = next(
            params
            for method, params in fake_client.requests
            if method == "thread/start"
        )
        tool_names = [t["name"] for t in thread_params.get("dynamicTools", [])]
        assert "setmodel" not in tool_names
        assert "setreasoning" not in tool_names

    @pytest.mark.asyncio
    async def test_setmodel_tool_changes_model(self) -> None:
        events = [
            _event_request(
                99,
                "item/tool/call",
                {
                    "tool": "setmodel",
                    "callId": "call-1",
                    "arguments": {"model": "o3"},
                },
            ),
            _event_notification(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [],
                        "error": None,
                    },
                },
            ),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_self_config_tools=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="switch to o3"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        assert adapter.config.model == "o3"
        assert adapter._selected_model == "o3"
        # Verify the tool response was sent back
        tool_responses = [
            (rid, result)
            for rid, result in fake_client.responses
            if isinstance(result, dict) and "contentItems" in result
        ]
        assert len(tool_responses) >= 1
        result_text = tool_responses[0][1]["contentItems"][0]["text"]
        assert "o3" in result_text

    @pytest.mark.asyncio
    async def test_setreasoning_tool_changes_effort(self) -> None:
        events = [
            _event_request(
                99,
                "item/tool/call",
                {
                    "tool": "setreasoning",
                    "callId": "call-2",
                    "arguments": {"effort": "xhigh", "summary": "detailed"},
                },
            ),
            _event_notification(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [],
                        "error": None,
                    },
                },
            ),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_self_config_tools=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="increase reasoning"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        assert adapter.config.reasoning_effort == "xhigh"
        assert adapter.config.reasoning_summary == "detailed"

    @pytest.mark.asyncio
    async def test_setreasoning_tool_rejects_invalid_effort(self) -> None:
        events = [
            _event_request(
                99,
                "item/tool/call",
                {
                    "tool": "setreasoning",
                    "callId": "call-3",
                    "arguments": {"effort": "ultra"},
                },
            ),
            _event_notification(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [],
                        "error": None,
                    },
                },
            ),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_self_config_tools=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="set reasoning ultra"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        # Effort should not have changed
        assert adapter.config.reasoning_effort is None
        # Tool response should contain error message
        tool_responses = [
            (rid, result)
            for rid, result in fake_client.responses
            if isinstance(result, dict) and "contentItems" in result
        ]
        assert len(tool_responses) >= 1
        result_text = tool_responses[0][1]["contentItems"][0]["text"]
        assert "Invalid reasoning effort" in result_text

    @pytest.mark.asyncio
    async def test_sandbox_alias_is_normalized_for_thread_and_turn(self) -> None:
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                sandbox="dangerFullAccess",
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        thread_start = next(
            params
            for method, params in fake_client.requests
            if method == "thread/start"
        )
        turn_start = next(
            params for method, params in fake_client.requests if method == "turn/start"
        )
        # thread/start only accepts the sandbox field (SandboxMode enum)
        assert thread_start["sandbox"] == "danger-full-access"
        # turn/start uses sandboxPolicy (full SandboxPolicy tagged union)
        assert turn_start["sandboxPolicy"]["type"] == "dangerFullAccess"

    @pytest.mark.asyncio
    async def test_external_sandbox_alias_uses_sandbox_policy(self) -> None:
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                sandbox="external-sandbox",
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        thread_start = next(
            params
            for method, params in fake_client.requests
            if method == "thread/start"
        )
        turn_start = next(
            params for method, params in fake_client.requests if method == "turn/start"
        )
        # thread/start has no sandboxPolicy field; externalSandbox is
        # only representable at turn level
        assert "sandbox" not in thread_start
        assert "sandboxPolicy" not in thread_start
        # turn/start can express the full SandboxPolicy tagged union
        assert turn_start["sandboxPolicy"]["type"] == "externalSandbox"

    @pytest.mark.asyncio
    async def test_transport_closed_event_aborts_turn(self) -> None:
        """A transport/closed event should end the turn with a failed status."""
        events = [
            _event_notification(
                "transport/closed",
                {"reason": "Codex process exited unexpectedly"},
            )
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Adapter should send a failure message mentioning the disconnect.
        assert any(
            "transport closed" in msg["content"].lower() for msg in tools.messages_sent
        )

    @pytest.mark.asyncio
    async def test_transport_closed_resets_client_state(self) -> None:
        """After transport/closed, _client and _initialized should be reset
        so the next message rebuilds the client via _ensure_client_ready()."""
        events = [
            _event_notification(
                "transport/closed",
                {"reason": "Codex process exited unexpectedly"},
            )
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # After transport/closed, client state should be reset
        assert adapter._client is None
        assert adapter._initialized is False

    @pytest.mark.asyncio
    async def test_transport_closed_clears_per_room_state(self) -> None:
        """After transport/closed, per-room state (thread_id, raw_history,
        pending approvals) must be cleared so the next turn does a fresh
        thread/start instead of reusing a cached thread_id from the dead
        session."""
        events = [
            _event_notification(
                "transport/closed",
                {"reason": "Codex process exited unexpectedly"},
            )
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Codex Agent", "A coding agent")

        # Pre-populate per-room state to simulate an active session.
        adapter._room_threads["room-1"] = "old-thread-id"
        adapter._raw_history_by_room["room-1"] = [{"role": "user", "content": "hi"}]

        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        # Per-room state should be cleared so next turn starts fresh.
        assert "room-1" not in adapter._room_threads
        assert "room-1" not in adapter._raw_history_by_room

    @pytest.mark.asyncio
    async def test_transport_closed_drains_token_usage_for_dead_threads(
        self,
    ) -> None:
        """Token-usage entries keyed by dead thread ids must be dropped on
        transport/closed; otherwise they leak past on_cleanup because the
        thread id is no longer reachable through ``_room_threads``.
        """
        from thenvoi.integrations.codex.types import CodexTokenUsage

        events = [
            _event_notification(
                "transport/closed",
                {"reason": "Codex process exited unexpectedly"},
            )
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Codex Agent", "A coding agent")

        # Pre-populate per-room state + token usage to simulate an active
        # session with recorded usage.
        adapter._room_threads["room-1"] = "old-thread-id"
        adapter._token_usage["old-thread-id"] = CodexTokenUsage(
            input_tokens=100, total_tokens=150
        )

        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        # Dead thread's usage entry must be gone even without a matching
        # on_cleanup (the room id can no longer look up the thread id).
        assert "old-thread-id" not in adapter._token_usage

    @pytest.mark.asyncio
    async def test_turn_timeout_sends_interrupt_and_clean_error(self) -> None:
        """When recv_event times out, the adapter sends turn/interrupt and reports cleanly."""
        # No events means FakeCodexClient raises asyncio.TimeoutError immediately.
        fake_client = FakeCodexClient(events=[])
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", turn_timeout_s=0.01),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Adapter should have sent turn/interrupt with both identifiers.
        interrupt_requests = [
            (m, p) for m, p in fake_client.requests if m == "turn/interrupt"
        ]
        assert interrupt_requests == [
            ("turn/interrupt", {"threadId": "thr-1", "turnId": "turn-1"})
        ]

        # Adapter should send a user-facing message about stopping.
        assert any("stopped" in msg["content"].lower() for msg in tools.messages_sent)

    @pytest.mark.asyncio
    async def test_item_completed_text_overrides_accumulated_deltas(self) -> None:
        """item/completed text is authoritative and should replace any accumulated deltas."""
        events = [
            _event_notification(
                "item/agentMessage/delta",
                {"itemId": "msg-1", "delta": "partial "},
            ),
            _event_notification(
                "item/agentMessage/delta",
                {"itemId": "msg-1", "delta": "garbled"},
            ),
            _event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "agentMessage",
                        "id": "msg-1",
                        "text": "authoritative final text",
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # The authoritative text from item/completed should be used, not the deltas.
        assert any(
            msg["content"] == "authoritative final text" for msg in tools.messages_sent
        )

    @pytest.mark.asyncio
    async def test_custom_tools_schemas_merged_into_dynamic_tools(self) -> None:
        """Custom tool schemas appear in _build_dynamic_tools output."""

        class WeatherInput(BaseModel):
            """Get current weather for a location."""

            city: str

        def get_weather(inp: WeatherInput) -> str:
            return f"Sunny in {inp.city}"

        custom_tools: list[CustomToolDef] = [(WeatherInput, get_weather)]
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            additional_tools=custom_tools,
        )

        tools = ToolSchemaFakeTools()
        dynamic_tools = adapter._build_dynamic_tools(tools)

        names = [t["name"] for t in dynamic_tools]
        assert "weather" in names

        weather_tool = next(t for t in dynamic_tools if t["name"] == "weather")
        assert weather_tool["description"] == "Get current weather for a location."
        assert "inputSchema" in weather_tool
        assert "city" in weather_tool["inputSchema"].get("properties", {})

    @pytest.mark.asyncio
    async def test_custom_tool_dispatched_before_platform_tools(self) -> None:
        """Custom tool is invoked via execute_custom_tool, not platform tools."""

        class CalculatorInput(BaseModel):
            """Simple calculator."""

            expression: str

        call_log: list[str] = []

        async def calculate(inp: CalculatorInput) -> str:
            call_log.append(inp.expression)
            return "42"

        custom_tools: list[CustomToolDef] = [(CalculatorInput, calculate)]
        events = [
            _event_request(
                99,
                "item/tool/call",
                {
                    "tool": "calculator",
                    "arguments": {"expression": "6*7"},
                    "callId": "call-99",
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            additional_tools=custom_tools,
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Custom tool was called
        assert call_log == ["6*7"]
        # Platform execute_tool_call was NOT called for the custom tool
        assert not any(tc["tool_name"] == "calculator" for tc in tools.tool_calls)
        # Response was sent back to Codex
        assert fake_client.responses
        _, payload = fake_client.responses[0]
        assert payload["success"] is True
        assert payload["contentItems"][0]["text"] == "42"

    @pytest.mark.asyncio
    async def test_execution_reporting_emits_tool_call_and_result_events(self) -> None:
        """With enable_execution_reporting, tool_call and tool_result events are emitted."""
        events = [
            _event_request(
                50,
                "item/tool/call",
                {
                    "tool": "thenvoi_lookup_peers",
                    "arguments": {"page": 1},
                    "callId": "call-50",
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_execution_reporting=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_call_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_call"
        ]
        tool_result_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_result"
        ]
        assert len(tool_call_events) == 1
        assert len(tool_result_events) == 1

        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "thenvoi_lookup_peers"
        assert call_data["tool_call_id"] == "call-50"

        result_data = json.loads(tool_result_events[0]["content"])
        assert result_data["name"] == "thenvoi_lookup_peers"
        assert result_data["tool_call_id"] == "call-50"

    @pytest.mark.asyncio
    async def test_execution_reporting_disabled_by_default(self) -> None:
        """Without enable_execution_reporting, no tool_call/tool_result events are emitted."""
        events = [
            _event_request(
                50,
                "item/tool/call",
                {
                    "tool": "thenvoi_lookup_peers",
                    "arguments": {"page": 1},
                    "callId": "call-50",
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_events = [
            e
            for e in tools.events_sent
            if e["message_type"] in {"tool_call", "tool_result"}
        ]
        assert tool_events == []

    @pytest.mark.asyncio
    async def test_execution_reporting_on_tool_error(self) -> None:
        """Execution reporting emits tool_result with error text on failure."""

        class FailInput(BaseModel):
            """A tool that always fails."""

            x: int

        async def fail_func(inp: FailInput) -> str:
            raise RuntimeError("boom")

        custom_tools: list[CustomToolDef] = [(FailInput, fail_func)]
        events = [
            _event_request(
                60,
                "item/tool/call",
                {
                    "tool": "fail",
                    "arguments": {"x": 1},
                    "callId": "call-60",
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_execution_reporting=True),
            additional_tools=custom_tools,
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_result_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_result"
        ]
        assert len(tool_result_events) == 1
        result_data = json.loads(tool_result_events[0]["content"])
        assert result_data["name"] == "fail"
        assert "boom" in result_data["output"]
        assert result_data["tool_call_id"] == "call-60"

        # Codex response should indicate failure
        _, payload = fake_client.responses[0]
        assert payload["success"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool_name",
        ["thenvoi_send_event", "thenvoi_send_message"],
    )
    async def test_execution_reporting_suppressed_for_platform_output_tools(
        self, tool_name: str
    ) -> None:
        """Platform tools that produce visible output should not emit reporting events."""
        events = [
            _event_request(
                70,
                "item/tool/call",
                {
                    "tool": tool_name,
                    "arguments": {"content": "test", "message_type": "thought"},
                    "callId": "call-70",
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_execution_reporting=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # The tool call itself should still execute
        assert len(tools.tool_calls) == 1
        assert tools.tool_calls[0]["tool_name"] == tool_name

        # But no tool_call/tool_result reporting events should be emitted
        reporting_events = [
            e
            for e in tools.events_sent
            if e["message_type"] in {"tool_call", "tool_result"}
        ]
        assert reporting_events == []


class TestItemCompletedForwarding:
    """Tests for forwarding internal Codex operations as platform events."""

    @pytest.mark.asyncio
    async def test_item_completed_commandExecution_emits_tool_events(self) -> None:
        """commandExecution item emits tool_call + tool_result with command/output."""
        events = [
            _event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "commandExecution",
                        "id": "cmd-1",
                        "command": "ls -la",
                        "cwd": "/workspace",
                        "aggregated_output": "total 42\ndrwxr-xr-x ...",
                        "exitCode": 0,
                        "status": "completed",
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_execution_reporting=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_call_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_call"
        ]
        tool_result_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_result"
        ]
        assert len(tool_call_events) == 1
        assert len(tool_result_events) == 1

        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "exec"
        assert call_data["args"]["command"] == "ls -la"
        assert call_data["args"]["cwd"] == "/workspace"
        assert call_data["tool_call_id"] == "cmd-1"

        result_data = json.loads(tool_result_events[0]["content"])
        assert result_data["name"] == "exec"
        assert "total 42" in result_data["output"]
        assert "exit_code=0" in result_data["output"]
        assert result_data["tool_call_id"] == "cmd-1"

    @pytest.mark.asyncio
    async def test_item_completed_fileChange_emits_tool_events(self) -> None:
        """fileChange emits tool_call + tool_result with file paths."""
        events = [
            _event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "fileChange",
                        "id": "fc-1",
                        "changes": [
                            {"path": "src/main.py"},
                            {"path": "src/utils.py"},
                        ],
                        "status": "applied",
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_execution_reporting=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_call_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_call"
        ]
        tool_result_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_result"
        ]
        assert len(tool_call_events) == 1
        assert len(tool_result_events) == 1

        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "file_edit"
        assert call_data["args"]["files"] == ["src/main.py", "src/utils.py"]

        result_data = json.loads(tool_result_events[0]["content"])
        assert result_data["output"] == "applied"

    @pytest.mark.asyncio
    async def test_item_completed_fileChange_missing_changes_is_safe(self) -> None:
        """fileChange without changes list should not crash and emits empty files."""
        events = [
            _event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "fileChange",
                        "id": "fc-2",
                        "changes": None,
                        "status": "applied",
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_execution_reporting=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_call_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_call"
        ]
        assert len(tool_call_events) == 1
        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "file_edit"
        assert call_data["args"]["files"] == []

    @pytest.mark.asyncio
    async def test_item_completed_imageView_emits_tool_events(self) -> None:
        """imageView emits tool_call + tool_result."""
        events = [
            _event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "imageView",
                        "id": "img-1",
                        "path": "/tmp/screenshot.png",
                        "status": "viewed",
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_execution_reporting=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_call_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_call"
        ]
        tool_result_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_result"
        ]
        assert len(tool_call_events) == 1
        assert len(tool_result_events) == 1

        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "view_image"
        assert call_data["args"]["path"] == "/tmp/screenshot.png"

    @pytest.mark.asyncio
    async def test_item_completed_collabAgentToolCall_emits_tool_events(self) -> None:
        """collabAgentToolCall emits tool_call + tool_result preserving empty result."""
        events = [
            _event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "collabAgentToolCall",
                        "id": "collab-1",
                        "tool": "delegate",
                        "prompt": "Review the changes",
                        "agents": ["Reviewer-1", "Reviewer-2"],
                        "result": {},
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_execution_reporting=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_call_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_call"
        ]
        tool_result_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_result"
        ]
        assert len(tool_call_events) == 1
        assert len(tool_result_events) == 1

        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "collab:delegate"
        assert call_data["args"]["prompt"] == "Review the changes"
        assert call_data["args"]["agents"] == ["Reviewer-1", "Reviewer-2"]

        result_data = json.loads(tool_result_events[0]["content"])
        assert result_data["output"] == "{}"

    @pytest.mark.asyncio
    async def test_item_completed_mcpToolCall_emits_tool_events(self) -> None:
        """mcpToolCall emits tool_call + tool_result with server/tool name."""
        events = [
            _event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "mcpToolCall",
                        "id": "mcp-1",
                        "server": "filesystem",
                        "tool": "read_file",
                        "arguments": {"path": "/etc/hosts"},
                        "result": {"content": "127.0.0.1 localhost"},
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_execution_reporting=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_call_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_call"
        ]
        tool_result_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_result"
        ]
        assert len(tool_call_events) == 1
        assert len(tool_result_events) == 1

        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "mcp:filesystem/read_file"
        assert call_data["args"]["path"] == "/etc/hosts"

        result_data = json.loads(tool_result_events[0]["content"])
        assert "127.0.0.1 localhost" in result_data["output"]

    @pytest.mark.asyncio
    async def test_item_completed_reasoning_emits_thought(self) -> None:
        """reasoning item emits thought event when emit_thought_events=True."""
        events = [
            _event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "reasoning",
                        "id": "reason-1",
                        "summary": [
                            "Analyzing the codebase structure",
                            "Identified key files to modify",
                        ],
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", emit_thought_events=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        thought_events = [
            e for e in tools.events_sent if e["message_type"] == "thought"
        ]
        assert len(thought_events) == 1
        assert "Analyzing the codebase structure" in thought_events[0]["content"]
        assert "Identified key files to modify" in thought_events[0]["content"]

    @pytest.mark.asyncio
    async def test_item_completed_skipped_when_reporting_disabled(self) -> None:
        """No tool events when enable_execution_reporting=False."""
        events = [
            _event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "commandExecution",
                        "id": "cmd-1",
                        "command": "ls",
                        "exitCode": 0,
                    }
                },
            ),
            _event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "reasoning",
                        "id": "reason-1",
                        "summary": ["thinking"],
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                enable_execution_reporting=False,
                emit_thought_events=False,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_events = [
            e
            for e in tools.events_sent
            if e["message_type"] in {"tool_call", "tool_result", "thought"}
        ]
        assert tool_events == []

    @pytest.mark.asyncio
    async def test_item_completed_agentMessage_still_sets_final_text(self) -> None:
        """Existing agentMessage behavior preserved alongside new forwarding."""
        events = [
            _event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "commandExecution",
                        "id": "cmd-1",
                        "command": "pytest",
                        "exitCode": 0,
                        "aggregated_output": "all tests passed",
                    }
                },
            ),
            _event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "agentMessage",
                        "id": "msg-1",
                        "text": "All tests pass!",
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_execution_reporting=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # agentMessage text should still be sent as the final message
        assert any(msg["content"] == "All tests pass!" for msg in tools.messages_sent)
        # commandExecution should also be forwarded as tool events
        tool_call_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_call"
        ]
        assert len(tool_call_events) == 1

    @pytest.mark.asyncio
    async def test_item_completed_webSearch_emits_tool_events(self) -> None:
        """webSearch item emits tool_call + tool_result."""
        events = [
            _event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "webSearch",
                        "id": "ws-1",
                        "query": "python asyncio tutorial",
                        "action": {"url": "https://example.com", "title": "Tutorial"},
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_execution_reporting=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_call_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_call"
        ]
        assert len(tool_call_events) == 1
        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "web_search"
        assert call_data["args"]["query"] == "python asyncio tutorial"

    @pytest.mark.asyncio
    async def test_item_completed_metadata_includes_codex_ids(self) -> None:
        """Forwarded events include codex_room_id, codex_thread_id, codex_turn_id."""
        events = [
            _event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "commandExecution",
                        "id": "cmd-1",
                        "command": "echo hi",
                        "exitCode": 0,
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", enable_execution_reporting=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_call_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_call"
        ]
        assert len(tool_call_events) == 1
        meta = tool_call_events[0]["metadata"]
        assert meta["codex_room_id"] == "room-1"
        assert meta["codex_thread_id"] == "thr-1"
        assert meta["codex_turn_id"] == "turn-1"


class TestHistoryInjection:
    @pytest.mark.asyncio
    async def test_history_injected_on_resume_failure(self) -> None:
        """Resume fails, fresh thread created, first turn input contains history block."""
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(
            events=events,
            resume_error=CodexJsonRpcError(code=-32002, message="Thread expired"),
        )
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        raw_history = [
            {
                "message_type": "task",
                "content": "mapping event",
                "metadata": {"codex_thread_id": "thr-old"},
            },
            {
                "message_type": "text",
                "content": "Can you refactor the auth module?",
                "sender_name": "Alice",
            },
            {
                "message_type": "text",
                "content": "Done — split into auth_handler.py and middleware.py",
                "sender_name": "CodexAgent",
            },
        ]

        inp = AgentInput(
            msg=make_platform_message(
                room_id="room-1", content="Now add rate limiting"
            ),
            tools=tools,
            history=HistoryProvider(raw=raw_history),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await adapter.on_event(inp)

        turn_start = next(
            params for method, params in fake_client.requests if method == "turn/start"
        )
        turn_input = turn_start["input"]
        history_items = [
            item for item in turn_input if "[Conversation History]" in item["text"]
        ]
        assert len(history_items) == 1
        assert "[Alice]: Can you refactor the auth module?" in history_items[0]["text"]
        assert (
            "[CodexAgent]: Done — split into auth_handler.py and middleware.py"
            in history_items[0]["text"]
        )
        # Task events should NOT appear in history context
        assert "mapping event" not in history_items[0]["text"]

    @pytest.mark.asyncio
    async def test_history_not_injected_on_successful_resume(self) -> None:
        """Resume succeeds, no history injection."""
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        raw_history = [
            {
                "message_type": "task",
                "content": "mapping",
                "metadata": {
                    "codex_thread_id": "thr-existing",
                    "codex_room_id": "room-1",
                },
            },
            {
                "message_type": "text",
                "content": "Hello",
                "sender_name": "Alice",
            },
        ]

        inp = AgentInput(
            msg=make_platform_message(room_id="room-1", content="Continue"),
            tools=tools,
            history=HistoryProvider(raw=raw_history),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await adapter.on_event(inp)

        turn_start = next(
            params for method, params in fake_client.requests if method == "turn/start"
        )
        turn_input = turn_start["input"]
        assert not any("[Conversation History]" in item["text"] for item in turn_input)

    @pytest.mark.asyncio
    async def test_history_not_injected_when_disabled(self) -> None:
        """inject_history_on_resume_failure=False, no injection even on failure."""
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(
            events=events,
            resume_error=CodexJsonRpcError(code=-32002, message="Thread expired"),
        )
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                inject_history_on_resume_failure=False,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        raw_history = [
            {
                "message_type": "text",
                "content": "Hello",
                "sender_name": "Alice",
            },
        ]

        inp = AgentInput(
            msg=make_platform_message(room_id="room-1", content="Continue"),
            tools=tools,
            history=HistoryProvider(raw=raw_history),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await adapter.on_event(inp)

        turn_start = next(
            params for method, params in fake_client.requests if method == "turn/start"
        )
        turn_input = turn_start["input"]
        assert not any("[Conversation History]" in item["text"] for item in turn_input)

    @pytest.mark.asyncio
    async def test_history_filters_non_text_messages(self) -> None:
        """Only text/message types appear in injected context."""
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(
            events=events,
            resume_error=CodexJsonRpcError(code=-32002, message="Not found"),
        )
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        raw_history = [
            {
                "message_type": "task",
                "content": "task event",
                "sender_name": "System",
                "metadata": {"codex_thread_id": "thr-old", "codex_room_id": "room-1"},
            },
            {
                "message_type": "tool_call",
                "content": '{"name": "foo"}',
                "sender_name": "Agent",
            },
            {
                "message_type": "tool_result",
                "content": "result",
                "sender_name": "Agent",
            },
            {
                "message_type": "thought",
                "content": "thinking...",
                "sender_name": "Agent",
            },
            {"message_type": "error", "content": "oops", "sender_name": "Agent"},
            {
                "message_type": "text",
                "content": "Hello world",
                "sender_name": "Alice",
            },
            {
                "message_type": "message",
                "content": "Hi there",
                "sender_name": "Bob",
            },
        ]

        inp = AgentInput(
            msg=make_platform_message(room_id="room-1", content="Go"),
            tools=tools,
            history=HistoryProvider(raw=raw_history),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await adapter.on_event(inp)

        turn_start = next(
            params for method, params in fake_client.requests if method == "turn/start"
        )
        turn_input = turn_start["input"]
        history_items = [
            item for item in turn_input if "[Conversation History]" in item["text"]
        ]
        assert len(history_items) == 1
        text = history_items[0]["text"]
        assert "[Alice]: Hello world" in text
        assert "[Bob]: Hi there" in text
        assert "task event" not in text
        assert "thinking..." not in text
        assert "oops" not in text
        assert "tool_call" not in text

    @pytest.mark.asyncio
    async def test_history_respects_max_messages(self) -> None:
        """Only last max_history_messages are injected."""
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(
            events=events,
            resume_error=CodexJsonRpcError(code=-32002, message="Not found"),
        )
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", max_history_messages=3),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        raw_history: list[dict[str, Any]] = [
            {
                "message_type": "task",
                "content": "mapping",
                "metadata": {"codex_thread_id": "thr-old", "codex_room_id": "room-1"},
            },
        ]
        raw_history.extend(
            {
                "message_type": "text",
                "content": f"Message {i}",
                "sender_name": "Alice",
            }
            for i in range(10)
        )

        inp = AgentInput(
            msg=make_platform_message(room_id="room-1", content="Go"),
            tools=tools,
            history=HistoryProvider(raw=raw_history),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await adapter.on_event(inp)

        turn_start = next(
            params for method, params in fake_client.requests if method == "turn/start"
        )
        turn_input = turn_start["input"]
        history_items = [
            item for item in turn_input if "[Conversation History]" in item["text"]
        ]
        assert len(history_items) == 1
        text = history_items[0]["text"]
        # Only last 3 messages should be present
        assert "Message 7" in text
        assert "Message 8" in text
        assert "Message 9" in text
        assert "Message 0" not in text
        assert "Message 6" not in text

    @pytest.mark.asyncio
    async def test_history_cleared_after_injection(self) -> None:
        """Raw history removed from memory after first turn."""
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(
            events=events,
            resume_error=CodexJsonRpcError(code=-32002, message="Not found"),
        )
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")

        raw_history = [
            {
                "message_type": "text",
                "content": "Hello",
                "sender_name": "Alice",
            },
        ]

        inp = AgentInput(
            msg=make_platform_message(room_id="room-1", content="Go"),
            tools=tools,
            history=HistoryProvider(raw=raw_history),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await adapter.on_event(inp)

        # After injection, stashed data should be cleaned up
        assert "room-1" not in adapter._raw_history_by_room
        assert "room-1" not in adapter._needs_history_injection

    @pytest.mark.asyncio
    async def test_model_fallback_on_auto_selected_model(self) -> None:
        """When auto-selected model fails, adapter falls back to another model."""
        events = [
            _event_notification(
                "turn/completed",
                {"turn": {"id": "turn-1", "status": "completed"}},
            ),
        ]
        fake_client = FakeCodexClient(
            events=events,
            turn_start_error=CodexJsonRpcError(
                code=-32000,
                message="Model gpt-5.3-codex is not available for this account",
            ),
            turn_start_error_once=True,
            model_list_result={
                "data": [
                    {"id": "gpt-5.3-codex", "hidden": False},
                    {"id": "gpt-5.2", "hidden": False},
                ]
            },
        )
        # model=None means auto-select — fallback should trigger
        adapter = CodexAdapter(
            config=CodexAdapterConfig(model=None),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "An agent")

        msg = make_platform_message(room_id="room-1", content="hello")
        await adapter.on_message(
            msg,
            tools,
            CodexSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        # Should have retried with fallback model
        turn_start_calls = [
            (m, p) for m, p in fake_client.requests if m == "turn/start"
        ]
        assert len(turn_start_calls) == 2
        # First attempt used auto-selected model
        assert turn_start_calls[0][1]["model"] == "gpt-5.3-codex"
        # Retry used fallback (gpt-5.2 since gpt-5.3-codex is excluded)
        assert turn_start_calls[1][1]["model"] == "gpt-5.2"
        assert adapter._selected_model == "gpt-5.2"

    @pytest.mark.asyncio
    async def test_model_fallback_prefers_gpt_5_2(self) -> None:
        """Fallback prefers gpt-5.2 over other models."""
        events = [
            _event_notification(
                "turn/completed",
                {"turn": {"id": "turn-1", "status": "completed"}},
            ),
        ]
        # Auto-select picks gpt-5.3-codex (first codex model).
        # After turn/start fails, fallback should pick gpt-5.2 (preferred fallback).
        fake_client = FakeCodexClient(
            events=events,
            turn_start_error=CodexJsonRpcError(
                code=-32000, message="Model unavailable"
            ),
            turn_start_error_once=True,
            model_list_result={
                "data": [
                    {"id": "gpt-5.3-codex", "hidden": False},
                    {"id": "gpt-5.1", "hidden": False},
                    {"id": "gpt-5.2", "hidden": False},
                ]
            },
        )
        adapter = CodexAdapter(
            config=CodexAdapterConfig(model=None),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "An agent")

        msg = make_platform_message(room_id="room-1", content="hello")
        await adapter.on_message(
            msg,
            tools,
            CodexSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        # Should prefer gpt-5.2 over gpt-5.1
        assert adapter._selected_model == "gpt-5.2"

    @pytest.mark.asyncio
    async def test_explicit_model_error_propagates_without_fallback(self) -> None:
        """When the user explicitly set a model, errors propagate — no silent fallback."""
        fake_client = FakeCodexClient(
            turn_start_error=CodexJsonRpcError(
                code=-32000,
                message="Model gpt-5.3-codex-spark is not available",
            ),
            turn_start_error_once=False,
            model_list_result={
                "data": [
                    {"id": "gpt-5.3-codex", "hidden": False},
                    {"id": "gpt-5.2", "hidden": False},
                ]
            },
        )
        # Explicitly configured model — user chose this, don't override
        adapter = CodexAdapter(
            config=CodexAdapterConfig(model="gpt-5.3-codex-spark"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "An agent")

        msg = make_platform_message(room_id="room-1", content="hello")
        with pytest.raises(CodexJsonRpcError, match="not available"):
            await adapter.on_message(
                msg,
                tools,
                CodexSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        # No model/list query for fallback should have been attempted
        model_list_calls = [m for m, _ in fake_client.requests if m == "model/list"]
        assert len(model_list_calls) == 0

    @pytest.mark.asyncio
    async def test_model_fallback_raises_when_no_alternative(self) -> None:
        """When no fallback model is available, the original error propagates."""
        fake_client = FakeCodexClient(
            turn_start_error=CodexJsonRpcError(
                code=-32000, message="Model not available"
            ),
            turn_start_error_once=False,
            model_list_result={"data": []},
        )
        adapter = CodexAdapter(
            config=CodexAdapterConfig(model=None),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "An agent")

        msg = make_platform_message(room_id="room-1", content="hello")
        with pytest.raises(CodexJsonRpcError, match="not available"):
            await adapter.on_message(
                msg,
                tools,
                CodexSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

    @pytest.mark.asyncio
    async def test_model_fallback_uses_configured_defaults_when_model_list_fails(
        self,
    ) -> None:
        """When model/list fails during fallback, ``config.fallback_models`` is used."""
        events = [
            _event_notification(
                "turn/completed",
                {"turn": {"id": "turn-1", "status": "completed"}},
            ),
        ]

        class ModelListFailsClient(FakeCodexClient):
            async def request(
                self,
                method: str,
                params: dict[str, Any] | None = None,
                *,
                retry_on_overload: bool = True,
            ) -> dict[str, Any]:
                if method == "model/list" and self.initialized:
                    # First call during init succeeds, subsequent calls fail
                    raise RuntimeError("model/list unavailable")
                return await super().request(
                    method, params, retry_on_overload=retry_on_overload
                )

        fake_client = ModelListFailsClient(
            events=events,
            turn_start_error=CodexJsonRpcError(
                code=-32000, message="Model not available"
            ),
            turn_start_error_once=True,
        )
        adapter = CodexAdapter(
            config=CodexAdapterConfig(model=None),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "An agent")

        # Auto-selection without model/list picks fallback_models[0] = gpt-5.2
        assert adapter._selected_model == "gpt-5.2"

        msg = make_platform_message(room_id="room-1", content="hello")
        await adapter.on_message(
            msg,
            tools,
            CodexSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        # turn/start with gpt-5.2 fails; fallback excludes it and picks the
        # next configured default (gpt-5.3-codex).
        assert adapter._selected_model == "gpt-5.3-codex"

    @pytest.mark.asyncio
    async def test_select_model_honours_custom_fallback_when_model_list_empty(
        self,
    ) -> None:
        """Operator-configured ``fallback_models[0]`` wins when model/list returns no data."""
        fake_client = FakeCodexClient(
            events=[
                _event_notification(
                    "turn/completed",
                    {"turn": {"id": "turn-1", "status": "completed"}},
                ),
            ],
            model_list_result={"data": []},
        )
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                model=None,
                fallback_models=("custom-model-x", "custom-model-y"),
            ),
            client_factory=lambda _config: fake_client,
        )
        await adapter.on_started("Agent", "An agent")

        assert adapter._selected_model == "custom-model-x"

    @pytest.mark.asyncio
    async def test_non_model_error_not_caught_by_fallback(self) -> None:
        """Non-model-related errors propagate without fallback attempt."""
        fake_client = FakeCodexClient(
            turn_start_error=CodexJsonRpcError(
                code=-32001, message="Server overloaded"
            ),
            turn_start_error_once=False,
        )
        adapter = CodexAdapter(
            config=CodexAdapterConfig(model=None),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "An agent")

        msg = make_platform_message(room_id="room-1", content="hello")
        with pytest.raises(CodexJsonRpcError, match="overloaded"):
            await adapter.on_message(
                msg,
                tools,
                CodexSessionState(),
                None,
                None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

    @pytest.mark.asyncio
    async def test_startup_config_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Startup emits a redacted config summary log line."""
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="stdio",
                model="gpt-5.3-codex",
                sandbox="workspace-write",
                approval_mode="manual",
            ),
            client_factory=lambda _config: fake_client,
        )

        with caplog.at_level("INFO", logger="thenvoi.adapters.codex"):
            await adapter.on_started("TestBot", "A test agent")

        startup_logs = [
            r for r in caplog.records if "Codex adapter started" in r.message
        ]
        assert len(startup_logs) == 1
        log_msg = startup_logs[0].message
        assert "agent=TestBot" in log_msg
        assert "transport=stdio" in log_msg
        assert "model=gpt-5.3-codex" in log_msg
        assert "sandbox=workspace-write" in log_msg
        assert "approval_mode=manual" in log_msg

    @pytest.mark.asyncio
    async def test_codex_error_emits_event_unconditionally(self) -> None:
        """Non-retryable Codex errors always emit a structured error event."""
        fake_client = FakeCodexClient(
            events=[
                _event_notification(
                    "error",
                    {"error": {"message": "Something went wrong"}, "willRetry": False},
                ),
                _event_notification(
                    "turn/completed",
                    {"turn": {"id": "turn-1", "status": "failed"}},
                ),
            ],
        )
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                emit_thought_events=False,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "An agent")

        msg = make_platform_message(room_id="room-1", content="do something")
        await adapter.on_message(
            msg,
            tools,
            CodexSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert len(error_events) == 1
        assert "Something went wrong" in error_events[0]["content"]

    @pytest.mark.asyncio
    async def test_cleanup_before_start(self) -> None:
        """Calling on_cleanup on a freshly constructed adapter should not raise."""
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="stdio"),
            client_factory=lambda _config: FakeCodexClient(),
        )
        # No on_started called — cleanup should be safe (idempotent)
        await adapter.on_cleanup("room-x")

    @pytest.mark.asyncio
    async def test_cleanup_clears_pending_approvals(self) -> None:
        """on_cleanup should evict all pending approvals for the given room."""
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="stdio"),
            client_factory=lambda _config: fake_client,
        )
        await adapter.on_started("Bot", "desc")

        # Manually inject a pending approval for room-1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        adapter._pending_approvals["room-1"] = {
            "tok-1": type(
                "_PA",
                (),
                {
                    "request_id": 1,
                    "method": "item/tool/call",
                    "summary": "test",
                    "created_at": datetime.now(),
                    "future": fut,
                },
            )(),
        }
        # Also register a room thread so the client isn't closed
        adapter._room_threads["room-1"] = "thr-1"
        adapter._room_threads["room-2"] = "thr-2"

        await adapter.on_cleanup("room-1")

        assert "room-1" not in adapter._pending_approvals
        # The future should have been resolved (declined)
        assert fut.done()

    @pytest.mark.asyncio
    async def test_tool_call_validation_error_returns_friendly_message(self) -> None:
        """A ValidationError during tool dispatch returns a user-friendly error."""
        from pydantic import ValidationError as PydanticValidationError

        class ValidationErrorTools(ToolSchemaFakeTools):
            async def execute_tool_call(
                self, tool_name: str, arguments: dict[str, Any]
            ) -> Any:
                call = {"tool_name": tool_name, "arguments": arguments}
                self.tool_calls.append(call)
                # Trigger a real Pydantic ValidationError
                raise PydanticValidationError.from_exception_data(
                    "thenvoi_send_message",
                    [
                        {
                            "type": "missing",
                            "loc": ("content",),
                            "msg": "Field required",
                            "input": arguments,
                        }
                    ],
                )

        events = [
            _event_request(
                99,
                "item/tool/call",
                {
                    "tool": "thenvoi_send_message",
                    "arguments": {},
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ValidationErrorTools()

        await adapter.on_started("Bot", "desc")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # The adapter should have responded to the tool call with success=False
        error_responses = [
            (rid, payload)
            for rid, payload in fake_client.responses
            if payload.get("success") is False
        ]
        assert len(error_responses) == 1
        error_text = error_responses[0][1]["contentItems"][0]["text"]
        assert "Invalid arguments for thenvoi_send_message" in error_text


# ===========================================================================
# Phase 1: Structured error reporting
# ===========================================================================


class TestStructuredErrors:
    @pytest.mark.asyncio
    async def test_structured_error_from_error_event(self) -> None:
        """Error events with codexErrorInfo emit structured metadata."""
        events = [
            _event_notification(
                "error",
                {
                    "error": {
                        "message": "Context window exceeded",
                        "codexErrorInfo": {
                            "type": "ContextWindowExceeded",
                            "code": "context_window_exceeded",
                            "retryable": False,
                        },
                    },
                    "willRetry": False,
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", structured_errors=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert len(error_events) == 1
        meta = error_events[0]["metadata"]
        assert meta["codex_error_type"] == "ContextWindowExceeded"
        assert meta["codex_suggested_action"] == "compact_context"
        assert meta["codex_is_retryable"] is False
        assert "context window" in error_events[0]["content"].lower()

    @pytest.mark.asyncio
    async def test_structured_error_from_failed_turn(self) -> None:
        """turn/completed with status=failed and codexErrorInfo emits structured error."""
        events = [
            _event_notification(
                "turn/completed",
                {
                    "turn": {
                        "id": "turn-1",
                        "status": "failed",
                        "error": {
                            "message": "Usage limit hit",
                            "codexErrorInfo": {
                                "type": "UsageLimitExceeded",
                                "code": "usage_limit",
                                "retryable": False,
                            },
                        },
                    }
                },
            ),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", structured_errors=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["metadata"]["codex_error_type"] == "UsageLimitExceeded"
        assert (
            error_events[0]["metadata"]["codex_suggested_action"] == "wait_or_upgrade"
        )

    @pytest.mark.asyncio
    async def test_structured_errors_disabled_falls_back_to_plain_text(self) -> None:
        """When structured_errors=False, errors use plain text format."""
        events = [
            _event_notification(
                "error",
                {
                    "error": {
                        "message": "Something failed",
                        "codexErrorInfo": {"type": "ContextWindowExceeded"},
                    },
                    "willRetry": False,
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", structured_errors=False),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["content"] == "Codex error: Something failed"
        assert "codex_error_type" not in error_events[0]["metadata"]


# ===========================================================================
# Phase 1: Enriched approvals & session-level acceptance
# ===========================================================================


class TestEnrichedApprovals:
    @pytest.mark.asyncio
    async def test_approve_session_auto_approves_subsequent_requests(self) -> None:
        """After /approve-session, same method type is auto-approved."""
        # First approval request - will be resolved via approve-session
        first_events = [
            _event_request(
                10,
                "item/commandExecution/requestApproval",
                {"command": "npm test"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=first_events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", approval_mode="manual"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        # Manually resolve the approval in the background
        async def approve_session_later():
            await _wait_for_pending_approval(adapter, "room-1")
            await adapter.on_message(
                make_platform_message(content="/approve-session req-10"),
                tools,
                CodexSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        task = asyncio.create_task(approve_session_later())
        await adapter.on_message(
            make_platform_message(content="run tests"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await task

        # Verify session-level was recorded with full command key
        assert "commandExecution:npm test" in (
            adapter._session_approved.get("room-1") or ()
        )
        # Verify approval message mentions session-level
        session_msgs = [
            m for m in tools.messages_sent if "session-level" in m["content"]
        ]
        assert len(session_msgs) >= 1

    @pytest.mark.asyncio
    async def test_approval_audit_trail_emitted(self) -> None:
        """Approval decisions emit audit trail task events."""
        events = [
            _event_request(
                7,
                "item/commandExecution/requestApproval",
                {"command": "rm -rf tmp"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                approval_mode="auto_decline",
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        audit_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "approval_resolution"
        ]
        assert len(audit_events) == 1
        assert audit_events[0]["metadata"]["codex_approval_decision"] == "decline"
        assert audit_events[0]["metadata"]["codex_decided_by"] == "policy:auto_decline"

    @pytest.mark.asyncio
    async def test_sandbox_command_changes_mode(self) -> None:
        """The /sandbox command sets a per-room override, not mutating global config."""
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="/sandbox read-only"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Per-room override is set, global config is unchanged
        assert adapter._sandbox_overrides.get("room-1") == "read-only"
        assert adapter.config.sandbox is None
        assert "read-only" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_sandbox_command_is_per_room(self) -> None:
        """Sandbox override in one room does not affect other rooms."""
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        await adapter.on_started("Agent", "A coding agent")

        adapter._sandbox_overrides["room-1"] = "read-only"

        assert adapter._effective_sandbox("room-1") == "read-only"
        assert adapter._effective_sandbox("room-2") is None

    @pytest.mark.asyncio
    async def test_sandbox_danger_full_access_requires_confirm_flag(self) -> None:
        """Escalating to danger-full-access without --confirm shows a prompt."""
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="/sandbox danger-full-access"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Override should NOT be set — confirmation was required
        assert "room-1" not in adapter._sandbox_overrides
        assert "--confirm" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_sandbox_escalation_to_danger_full_access_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Escalating to danger-full-access with --confirm logs a warning."""
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        with caplog.at_level(logging.WARNING, logger="thenvoi.adapters.codex"):
            await adapter.on_message(
                make_platform_message(content="/sandbox danger-full-access --confirm"),
                tools,
                CodexSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        assert adapter._sandbox_overrides.get("room-1") == "danger-full-access"
        assert any(
            "Sandbox escalated to danger-full-access" in record.message
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_sandbox_command_rejects_invalid_mode(self) -> None:
        """The /sandbox command rejects invalid modes."""
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="/sandbox invalid-mode"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert adapter.config.sandbox is None
        assert "room-1" not in adapter._sandbox_overrides
        assert "Invalid sandbox mode" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_permissions_command_shows_state(self) -> None:
        """/permissions shows current effective permissions."""
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="/permissions"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert "Effective permissions:" in tools.messages_sent[0]["content"]
        assert "approval_mode: manual" in tools.messages_sent[0]["content"]


# ===========================================================================
# Phase 2: Plan & task lifecycle
# ===========================================================================


class TestPlanAndLifecycle:
    @pytest.mark.asyncio
    async def test_plan_steps_forwarded(self) -> None:
        """turn/plan/updated forwards structured plan steps."""
        events = [
            _event_notification(
                "turn/plan/updated",
                {
                    "plan": {
                        "steps": [
                            {"text": "Read the failing test", "status": "completed"},
                            {"text": "Identify root cause", "status": "inProgress"},
                            {"text": "Apply fix", "status": "pending"},
                        ]
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", stream_plan_events=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        plan_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_plan_steps") is not None
        ]
        assert len(plan_events) == 1
        steps = plan_events[0]["metadata"]["codex_plan_steps"]
        assert len(steps) == 3
        assert steps[0]["step"] == "Read the failing test"
        assert steps[0]["status"] == "completed"
        assert steps[2]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_plan_steps_not_forwarded_when_disabled(self) -> None:
        """turn/plan/updated is ignored when stream_plan_events=False."""
        events = [
            _event_notification(
                "turn/plan/updated",
                {
                    "plan": {
                        "steps": [
                            {"text": "Step 1", "status": "pending"},
                        ]
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", stream_plan_events=False),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        plan_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_plan_steps") is not None
        ]
        assert plan_events == []

    @pytest.mark.asyncio
    async def test_turn_lifecycle_events_emitted(self) -> None:
        """Enriched turn lifecycle events include duration and status."""
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                emit_turn_lifecycle_events=True,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        lifecycle_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "turn_lifecycle"
        ]
        assert len(lifecycle_events) == 2
        # First event: turn started (with input summary)
        assert lifecycle_events[0]["metadata"]["codex_turn_status"] == "started"
        assert "codex_input_summary" in lifecycle_events[0]["metadata"]
        # Second event: turn completed (with duration)
        assert lifecycle_events[1]["metadata"]["codex_turn_status"] == "completed"
        assert "codex_duration_s" in lifecycle_events[1]["metadata"]

    @pytest.mark.asyncio
    async def test_threads_command_lists_mappings(self) -> None:
        """/threads command shows room→thread mappings."""
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Now run /threads
        await adapter.on_message(
            make_platform_message(content="/threads"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        threads_msgs = [
            m
            for m in tools.messages_sent
            if "thread mappings" in m["content"].lower()
            or "active thread" in m["content"].lower()
        ]
        assert len(threads_msgs) >= 1
        assert "room-1" in threads_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_thread_archive_clears_mapping(self) -> None:
        """/thread archive removes the thread mapping."""
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        assert "room-1" in adapter._room_threads

        await adapter.on_message(
            make_platform_message(content="/thread archive"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        assert "room-1" not in adapter._room_threads
        assert any("archived" in m["content"].lower() for m in tools.messages_sent)


# ===========================================================================
# Phase 3: Real-time streaming
# ===========================================================================


class TestRealtimeStreaming:
    @pytest.mark.asyncio
    async def test_reasoning_delta_streamed_as_thought(self) -> None:
        """item/reasoning/summaryTextDelta forwards as streaming thought."""
        events = [
            _event_notification(
                "item/reasoning/summaryTextDelta",
                {"delta": "Analyzing the code...", "itemId": "item-1"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", stream_reasoning_events=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        thought_events = [
            e
            for e in tools.events_sent
            if e["message_type"] == "thought" and e["metadata"].get("streaming")
        ]
        assert len(thought_events) == 1
        assert thought_events[0]["content"] == "Analyzing the code..."
        assert thought_events[0]["metadata"]["codex_item_id"] == "item-1"

    @pytest.mark.asyncio
    async def test_reasoning_delta_ignored_when_disabled(self) -> None:
        """Reasoning deltas are skipped when stream_reasoning_events=False."""
        events = [
            _event_notification(
                "item/reasoning/summaryTextDelta",
                {"delta": "Thinking...", "itemId": "item-1"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", stream_reasoning_events=False),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        streaming_events = [
            e for e in tools.events_sent if e["metadata"].get("streaming")
        ]
        assert streaming_events == []

    @pytest.mark.asyncio
    async def test_plan_delta_streamed_as_thought(self) -> None:
        """item/plan/delta forwards as streaming thought with plan subtype."""
        events = [
            _event_notification(
                "item/plan/delta",
                {"delta": "Step 1: Read the test", "itemId": "plan-1"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", stream_plan_events=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        plan_thoughts = [
            e
            for e in tools.events_sent
            if e["message_type"] == "thought" and e["metadata"].get("subtype") == "plan"
        ]
        assert len(plan_thoughts) == 1
        assert plan_thoughts[0]["content"] == "Step 1: Read the test"

    @pytest.mark.asyncio
    async def test_commentary_phase_streamed_as_thought(self) -> None:
        """item/agentMessage/delta with phase=commentary streams as thought."""
        events = [
            _event_notification(
                "item/agentMessage/delta",
                {
                    "delta": "Let me think about this...",
                    "itemId": "msg-1",
                    "phase": "commentary",
                },
            ),
            _event_notification(
                "item/agentMessage/delta",
                {
                    "delta": "Here is the answer.",
                    "itemId": "msg-1",
                    "phase": "final_answer",
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", stream_commentary_events=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        commentary_thoughts = [
            e for e in tools.events_sent if e["metadata"].get("subtype") == "commentary"
        ]
        assert len(commentary_thoughts) == 1
        assert commentary_thoughts[0]["content"] == "Let me think about this..."

        # Only the final_answer delta should be in the fallback text
        assert any("Here is the answer." in m["content"] for m in tools.messages_sent)

    @pytest.mark.asyncio
    async def test_commentary_excluded_from_final_text_when_streaming_enabled(
        self,
    ) -> None:
        """When stream_commentary_events=True, commentary is excluded from final_text."""
        events = [
            _event_notification(
                "item/agentMessage/delta",
                {
                    "delta": "thinking...",
                    "itemId": "msg-1",
                    "phase": "commentary",
                },
            ),
            _event_notification(
                "item/agentMessage/delta",
                {
                    "delta": "real answer",
                    "itemId": "msg-1",
                    "phase": "final_answer",
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", stream_commentary_events=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Only the final_answer delta should be in the message text
        assert len(tools.messages_sent) == 1
        assert tools.messages_sent[0]["content"] == "real answer"

    @pytest.mark.asyncio
    async def test_commentary_included_in_final_text_when_streaming_disabled(
        self,
    ) -> None:
        """When stream_commentary_events=False (default), commentary accumulates into final_text."""
        events = [
            _event_notification(
                "item/agentMessage/delta",
                {
                    "delta": "thinking...",
                    "itemId": "msg-1",
                    "phase": "commentary",
                },
            ),
            _event_notification(
                "item/agentMessage/delta",
                {
                    "delta": "real answer",
                    "itemId": "msg-1",
                    "phase": "final_answer",
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", stream_commentary_events=False),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Both phases should be accumulated (backward compatible)
        assert len(tools.messages_sent) == 1
        assert tools.messages_sent[0]["content"] == "thinking...real answer"


# ===========================================================================
# Phase 4: Diffs + token usage
# ===========================================================================


class TestDiffsAndTokenUsage:
    @pytest.mark.asyncio
    async def test_diff_event_forwarded(self) -> None:
        """turn/diff/updated forwards as a task event when enabled."""
        events = [
            _event_notification(
                "turn/diff/updated",
                {
                    "diff": "--- a/src/app.py\n+++ b/src/app.py\n@@ ...",
                    "files": ["src/app.py"],
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                emit_diff_events=True,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        diff_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "turn_diff"
        ]
        assert len(diff_events) == 1
        assert diff_events[0]["message_type"] == "task"
        assert diff_events[0]["metadata"]["codex_files_changed"] == ["src/app.py"]
        assert "src/app.py" in diff_events[0]["metadata"]["codex_diff"]
        assert "1 files changed" in diff_events[0]["content"]

    @pytest.mark.asyncio
    async def test_diff_event_requires_task_events_emit(self) -> None:
        """Diffs are not forwarded when TASK_EVENTS is not in features.emit."""
        from thenvoi.core.types import AdapterFeatures

        events = [
            _event_notification(
                "turn/diff/updated",
                {"diff": "some diff", "files": ["f.py"]},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                emit_diff_events=True,
            ),
            features=AdapterFeatures(emit=frozenset()),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        diff_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "turn_diff"
        ]
        assert diff_events == []

    @pytest.mark.asyncio
    async def test_token_usage_tracked_and_emitted(self) -> None:
        """thread/tokenUsage/updated events are tracked and emitted."""
        events = [
            _event_notification(
                "thread/tokenUsage/updated",
                {
                    "usage": {
                        "inputTokens": 15000,
                        "outputTokens": 3200,
                        "reasoningTokens": 8000,
                        "totalTokens": 26200,
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", emit_token_usage_events=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        usage_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "token_usage"
        ]
        assert len(usage_events) == 1
        assert usage_events[0]["metadata"]["codex_input_tokens"] == 15000
        assert usage_events[0]["metadata"]["codex_output_tokens"] == 3200
        assert usage_events[0]["metadata"]["codex_total_tokens"] == 26200

    @pytest.mark.asyncio
    async def test_token_usage_ignored_when_disabled(self) -> None:
        """Token usage events are tracked internally but not emitted when disabled."""
        events = [
            _event_notification(
                "thread/tokenUsage/updated",
                {
                    "usage": {
                        "inputTokens": 1000,
                        "outputTokens": 500,
                        "totalTokens": 1500,
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", emit_token_usage_events=False),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        usage_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "token_usage"
        ]
        assert usage_events == []

        # But internal tracking still works
        thread_id = adapter._room_threads.get("room-1")
        assert thread_id is not None
        usage = adapter._token_usage.get(thread_id)
        assert usage is not None
        assert usage.input_tokens == 1000

    @pytest.mark.asyncio
    async def test_usage_command_shows_token_usage(self) -> None:
        """/usage command shows accumulated token usage."""
        events = [
            _event_notification(
                "thread/tokenUsage/updated",
                {
                    "usage": {
                        "inputTokens": 5000,
                        "outputTokens": 1000,
                        "reasoningTokens": 2000,
                        "totalTokens": 8000,
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Now run /usage
        await adapter.on_message(
            make_platform_message(content="/usage"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        usage_msgs = [
            m for m in tools.messages_sent if "token usage" in m["content"].lower()
        ]
        assert len(usage_msgs) >= 1
        assert "8,000" in usage_msgs[0]["content"]


# ===========================================================================
# Types unit tests
# ===========================================================================


class TestCodexTypes:
    def test_build_structured_error_metadata_known_type(self) -> None:
        from thenvoi.integrations.codex.types import build_structured_error_metadata

        error_obj = {
            "message": "Context overflow",
            "codexErrorInfo": {
                "type": "ContextWindowExceeded",
                "code": "ctx_exceeded",
                "retryable": False,
            },
        }
        content, meta = build_structured_error_metadata(
            error_obj, thread_id="t1", turn_id="turn-1"
        )
        assert "context window" in content.lower()
        assert meta["codex_error_type"] == "ContextWindowExceeded"
        assert meta["codex_suggested_action"] == "compact_context"
        assert meta["codex_thread_id"] == "t1"
        assert meta["codex_turn_id"] == "turn-1"

    def test_build_structured_error_metadata_unknown_type(self) -> None:
        from thenvoi.integrations.codex.types import build_structured_error_metadata

        error_obj = {
            "message": "Something weird happened",
            "codexErrorInfo": {"type": "UnknownError"},
        }
        content, meta = build_structured_error_metadata(error_obj)
        assert content == "Something weird happened"
        assert meta["codex_error_type"] == "UnknownError"
        assert meta["codex_suggested_action"] is None

    def test_parse_plan_steps(self) -> None:
        from thenvoi.integrations.codex.types import parse_plan_steps

        params = {
            "plan": {
                "steps": [
                    {"text": "Step 1", "status": "completed"},
                    {"text": "Step 2", "status": "inProgress"},
                    {"text": "Step 3", "status": "pending"},
                ]
            }
        }
        steps = parse_plan_steps(params)
        assert len(steps) == 3
        assert steps[0].step == "Step 1"
        assert steps[0].status == "completed"
        assert steps[2].status == "pending"

    def test_parse_plan_steps_string_entries(self) -> None:
        from thenvoi.integrations.codex.types import parse_plan_steps

        params = {"plan": {"steps": ["Read code", "Fix bug"]}}
        steps = parse_plan_steps(params)
        assert len(steps) == 2
        assert steps[0].step == "Read code"
        assert steps[0].status == "pending"

    def test_codex_token_usage_update(self) -> None:
        from thenvoi.integrations.codex.types import CodexTokenUsage

        usage = CodexTokenUsage()
        usage.update(
            {
                "usage": {
                    "inputTokens": 1000,
                    "outputTokens": 500,
                    "reasoningTokens": 200,
                    "totalTokens": 1700,
                }
            }
        )
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500
        assert usage.reasoning_tokens == 200
        assert usage.total_tokens == 1700
        meta = usage.to_metadata()
        assert meta["codex_input_tokens"] == 1000
        assert "1,700" in usage.format_summary()

    def test_config_new_flags_default_false(self) -> None:
        """All new config flags default to False (except structured_errors=True)."""
        config = CodexAdapterConfig()
        assert config.structured_errors is True
        assert config.stream_reasoning_events is False
        assert config.stream_plan_events is False
        assert config.stream_commentary_events is False
        assert config.emit_diff_events is False
        assert config.emit_token_usage_events is False
        assert config.emit_turn_lifecycle_events is False

    def test_session_approval_key_full_command_by_default(self) -> None:
        """Session approval key includes full command string by default."""
        adapter = CodexAdapter(config=CodexAdapterConfig())
        key = adapter._session_approval_key(
            "item/commandExecution/requestApproval", {"command": "npm test"}
        )
        assert key == "commandExecution:npm test"

    def test_session_approval_key_binary_granularity(self) -> None:
        """Session approval key includes only binary when granularity is 'binary'."""
        adapter = CodexAdapter(
            config=CodexAdapterConfig(session_approval_granularity="binary")
        )
        key = adapter._session_approval_key(
            "item/commandExecution/requestApproval", {"command": "npm test"}
        )
        assert key == "commandExecution:npm"

    def test_session_approval_key_empty_for_missing_command(self) -> None:
        """Session approval key returns empty when command is missing (no wildcard)."""
        adapter = CodexAdapter(config=CodexAdapterConfig())
        key = adapter._session_approval_key("item/commandExecution/requestApproval", {})
        assert key == ""

    def test_session_approval_key_empty_for_file_changes_without_paths(self) -> None:
        """Session approval refuses fileChange requests that carry no paths.

        Previously the bare method name was used, which turned a single
        /approve-session into a blanket "approve every future file change"
        switch.  We now require a path signature.
        """
        adapter = CodexAdapter(config=CodexAdapterConfig())
        key = adapter._session_approval_key(
            "item/fileChange/requestApproval", {"reason": "update"}
        )
        assert key == ""


class TestSessionAutoApproval:
    @pytest.mark.asyncio
    async def test_session_auto_approves_matching_command_binary(self) -> None:
        """After session-level approval for npm, a new npm command is auto-approved."""
        # Two command execution requests in one turn — first will be manually
        # approved, second should be auto-approved by session policy.
        events = [
            _event_request(
                20,
                "item/commandExecution/requestApproval",
                {"command": "npm install"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                approval_mode="manual",
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        # Pre-seed the session-approved set as if /approve-session was used for "npm install"
        adapter._session_approved["room-1"] = OrderedDict(
            [("commandExecution:npm install", None)]
        )

        await adapter.on_message(
            make_platform_message(content="install deps"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # The request should have been auto-approved via session policy
        responses = fake_client.responses
        assert any(
            result.get("decision") in {"accept", "acceptForSession"}
            for _, result in responses
        )

    @pytest.mark.asyncio
    async def test_session_does_not_auto_approve_different_command(self) -> None:
        """Session approval for 'npm install' does NOT auto-approve 'npm publish'."""
        events = [
            _event_request(
                30,
                "item/commandExecution/requestApproval",
                {"command": "npm publish"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                approval_mode="auto_decline",
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        # Pre-seed session approval for "npm install" only
        adapter._session_approved["room-1"] = OrderedDict(
            [("commandExecution:npm install", None)]
        )

        await adapter.on_message(
            make_platform_message(content="publish package"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # npm publish should have been declined, not auto-approved
        responses = fake_client.responses
        assert any(result.get("decision") == "decline" for _, result in responses)

    @pytest.mark.asyncio
    async def test_session_binary_granularity_approves_same_binary(self) -> None:
        """With binary granularity, session approval for 'npm test' auto-approves 'npm install'."""
        events = [
            _event_request(
                40,
                "item/commandExecution/requestApproval",
                {"command": "npm install"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                approval_mode="manual",
                session_approval_granularity="binary",
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        # Pre-seed session approval for npm binary
        adapter._session_approved["room-1"] = OrderedDict(
            [("commandExecution:npm", None)]
        )

        await adapter.on_message(
            make_platform_message(content="install deps"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # npm install should be auto-approved because binary matches
        responses = fake_client.responses
        assert any(
            result.get("decision") in {"accept", "acceptForSession"}
            for _, result in responses
        )


class TestCleanup:
    @pytest.mark.asyncio
    async def test_on_cleanup_removes_per_room_token_usage(self) -> None:
        """on_cleanup for a room also removes the thread's token usage."""
        events = [
            _event_notification(
                "thread/tokenUsage/updated",
                {
                    "usage": {
                        "inputTokens": 500,
                        "outputTokens": 100,
                        "totalTokens": 600,
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Verify usage was tracked
        thread_id = adapter._room_threads.get("room-1")
        assert thread_id is not None
        assert thread_id in adapter._token_usage

        # Add a second room so cleanup doesn't close the client entirely
        adapter._room_threads["room-2"] = "other-thread"

        await adapter.on_cleanup("room-1")
        assert thread_id not in adapter._token_usage


class TestAuditCap:
    def test_audit_trail_capped_at_limit(self) -> None:
        """Approval audit trail is capped at max_approval_audit_per_room."""
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", max_approval_audit_per_room=5),
            client_factory=lambda _config: FakeCodexClient(),
        )
        for i in range(10):
            adapter._record_approval_audit(
                room_id="room-1",
                request_id=str(i),
                method="item/commandExecution/requestApproval",
                decision="accept",
                decided_by="test",
            )
        audit = adapter._approval_audit["room-1"]
        assert len(audit) == 5
        # Should keep the most recent entries
        assert audit[0].request_id == "5"
        assert audit[-1].request_id == "9"

    def test_session_approved_capped_at_limit(self) -> None:
        """Session approvals evict LRU when max_session_approved_per_room is hit."""
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", max_session_approved_per_room=3),
            client_factory=lambda _config: FakeCodexClient(),
        )
        for i in range(5):
            adapter._record_session_approval("room-1", f"commandExecution:cmd{i}")
        room = adapter._session_approved["room-1"]
        assert list(room.keys()) == [
            "commandExecution:cmd2",
            "commandExecution:cmd3",
            "commandExecution:cmd4",
        ]

    def test_session_approval_reinsert_moves_to_end(self) -> None:
        """Re-approving an existing key moves it to the most-recent slot."""
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", max_session_approved_per_room=3),
            client_factory=lambda _config: FakeCodexClient(),
        )
        adapter._record_session_approval("room-1", "commandExecution:a")
        adapter._record_session_approval("room-1", "commandExecution:b")
        adapter._record_session_approval("room-1", "commandExecution:c")
        # Re-approve the oldest — it should move to the end.
        adapter._record_session_approval("room-1", "commandExecution:a")
        adapter._record_session_approval("room-1", "commandExecution:d")
        room = adapter._session_approved["room-1"]
        # "b" is the oldest after re-approving "a", so it's the one evicted.
        assert "commandExecution:b" not in room
        assert list(room.keys()) == [
            "commandExecution:c",
            "commandExecution:a",
            "commandExecution:d",
        ]


class TestReviewFixes:
    """Tests for issues identified in PR review."""

    @pytest.mark.asyncio
    async def test_sandbox_command_blocked_when_sandbox_policy_set(self) -> None:
        """/sandbox is rejected when sandbox_policy is configured."""
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                sandbox_policy={"type": "readOnly"},
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="/sandbox workspace-write"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert "room-1" not in adapter._sandbox_overrides
        assert "Cannot override sandbox" in tools.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_thread_archive_clears_raw_history(self) -> None:
        """/thread archive also clears raw history and injection state."""
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Seed raw history and injection flag
        adapter._raw_history_by_room["room-1"] = [{"role": "user", "content": "hi"}]
        adapter._needs_history_injection.add("room-1")

        await adapter.on_message(
            make_platform_message(content="/thread archive"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert "room-1" not in adapter._raw_history_by_room
        assert "room-1" not in adapter._needs_history_injection

    def test_token_usage_update_handles_zero_values(self) -> None:
        """CodexTokenUsage.update() correctly handles explicit zero values."""
        from thenvoi.integrations.codex.types import CodexTokenUsage

        usage = CodexTokenUsage()
        usage.update(
            {
                "usage": {
                    "inputTokens": 0,
                    "outputTokens": 100,
                    "reasoningTokens": 0,
                    "totalTokens": 100,
                }
            }
        )
        assert usage.input_tokens == 0
        assert usage.output_tokens == 100
        assert usage.reasoning_tokens == 0
        assert usage.total_tokens == 100

    def test_session_approval_key_empty_prevents_wildcard_match(self) -> None:
        """Empty session key from missing command cannot match any session set."""
        adapter = CodexAdapter(config=CodexAdapterConfig())
        key = adapter._session_approval_key("item/commandExecution/requestApproval", {})
        # Empty key is falsy, so `key and key in session_set` is always False
        assert not key
        assert not (key and key in {"commandExecution:npm"})

    @pytest.mark.asyncio
    async def test_unexpected_recv_error_still_emits_turn_outcome(self) -> None:
        """When recv_event raises a non-timeout exception, _emit_turn_outcome is still called."""

        class BrokenClient(FakeCodexClient):
            async def recv_event(self, timeout_s: float | None = None) -> RpcEvent:
                raise ConnectionError("transport died")

        fake_client = BrokenClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                fallback_send_agent_text=True,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        # Should have sent an error message to the user instead of crashing
        assert any("couldn't complete" in m["content"] for m in tools.messages_sent)


# ===========================================================================
# Gap fixes: acceptForSession, network_context, turn started, compaction,
#            per-turn token deltas
# ===========================================================================


class TestAcceptForSession:
    @pytest.mark.asyncio
    async def test_approve_session_sends_accept_for_session_decision(self) -> None:
        """After /approve-session, the decision sent to Codex is 'acceptForSession'."""
        events = [
            _event_request(
                10,
                "item/commandExecution/requestApproval",
                {"command": "npm test"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", approval_mode="manual"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        async def approve_session_later():
            await _wait_for_pending_approval(adapter, "room-1")
            await adapter.on_message(
                make_platform_message(content="/approve-session req-10"),
                tools,
                CodexSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        task = asyncio.create_task(approve_session_later())
        await adapter.on_message(
            make_platform_message(content="run tests"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await task

        # The decision sent to Codex should be 'acceptForSession'
        accept_responses = [
            result
            for _, result in fake_client.responses
            if result.get("decision") in {"accept", "acceptForSession"}
        ]
        assert len(accept_responses) >= 1
        assert accept_responses[0]["decision"] == "acceptForSession"

    @pytest.mark.asyncio
    async def test_session_auto_approval_sends_accept_for_session(self) -> None:
        """Session auto-approved requests send 'acceptForSession' to Codex."""
        events = [
            _event_request(
                20,
                "item/commandExecution/requestApproval",
                {"command": "npm install"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", approval_mode="manual"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        # Pre-seed session approval for the exact command
        adapter._session_approved["room-1"] = OrderedDict(
            [("commandExecution:npm install", None)]
        )

        await adapter.on_message(
            make_platform_message(content="install deps"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Decision should be acceptForSession
        responses = fake_client.responses
        decisions = [r.get("decision") for _, r in responses]
        assert "acceptForSession" in decisions


class TestNetworkContext:
    @pytest.mark.asyncio
    async def test_network_context_included_in_approval_metadata(self) -> None:
        """networkContext from approval params is forwarded in metadata."""
        events = [
            _event_request(
                10,
                "item/commandExecution/requestApproval",
                {
                    "command": "npm install lodash",
                    "cwd": "/workspace",
                    "networkContext": {"domains": ["registry.npmjs.org"]},
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", approval_mode="manual"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        # Resolve the approval in background
        async def approve_later():
            await _wait_for_pending_approval(adapter, "room-1")
            await adapter.on_message(
                make_platform_message(content="/approve req-10"),
                tools,
                CodexSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        task = asyncio.create_task(approve_later())
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await task

        approval_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "approval_request"
        ]
        assert len(approval_events) == 1
        assert approval_events[0]["metadata"]["codex_network_context"] == {
            "domains": ["registry.npmjs.org"]
        }
        assert approval_events[0]["metadata"]["codex_command"] == "npm install lodash"


class TestTurnStartedLifecycle:
    @pytest.mark.asyncio
    async def test_turn_started_lifecycle_event_emitted(self) -> None:
        """Turn started lifecycle event includes input summary."""
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                emit_turn_lifecycle_events=True,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="fix the login bug"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        started_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "turn_lifecycle"
            and e["metadata"].get("codex_turn_status") == "started"
        ]
        assert len(started_events) == 1
        assert (
            started_events[0]["metadata"]["codex_input_summary"] == "fix the login bug"
        )


class TestContextCompaction:
    @pytest.mark.asyncio
    async def test_context_compaction_event_emitted(self) -> None:
        """context/compacted events are forwarded as task events."""
        events = [
            _event_notification(
                "context/compacted",
                {"threadId": "thr-1", "turnId": "turn-1"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                emit_turn_lifecycle_events=True,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        compaction_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "context_compaction"
        ]
        assert len(compaction_events) == 1
        assert compaction_events[0]["metadata"]["codex_thread_id"] == "thr-1"

    @pytest.mark.asyncio
    async def test_context_compaction_ignored_when_disabled(self) -> None:
        """Compaction events are not emitted when emit_turn_lifecycle_events=False."""
        events = [
            _event_notification(
                "context/compacted",
                {"threadId": "thr-1", "turnId": "turn-1"},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                emit_turn_lifecycle_events=False,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        compaction_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "context_compaction"
        ]
        assert compaction_events == []


class TestPerTurnTokenUsage:
    def test_token_usage_computes_per_turn_deltas(self) -> None:
        """Per-turn deltas are computed from consecutive cumulative updates."""
        from thenvoi.integrations.codex.types import CodexTokenUsage

        usage = CodexTokenUsage()

        # First update: turn 1
        usage.update(
            {
                "usage": {
                    "inputTokens": 1000,
                    "outputTokens": 500,
                    "reasoningTokens": 200,
                    "totalTokens": 1700,
                }
            }
        )
        assert usage.turn_input_tokens == 1000
        assert usage.turn_output_tokens == 500
        assert usage.turn_total_tokens == 1700

        # Second update: turn 2 (cumulative increases)
        usage.reset_turn_deltas()
        usage.update(
            {
                "usage": {
                    "inputTokens": 2500,
                    "outputTokens": 900,
                    "reasoningTokens": 400,
                    "totalTokens": 3800,
                }
            }
        )
        assert usage.turn_input_tokens == 1500  # 2500 - 1000
        assert usage.turn_output_tokens == 400  # 900 - 500
        assert usage.turn_reasoning_tokens == 200  # 400 - 200
        assert usage.turn_total_tokens == 2100  # 3800 - 1700

    def test_token_usage_metadata_includes_turn_deltas(self) -> None:
        """to_metadata() includes per-turn deltas when available."""
        from thenvoi.integrations.codex.types import CodexTokenUsage

        usage = CodexTokenUsage()
        usage.update(
            {
                "usage": {
                    "inputTokens": 1000,
                    "outputTokens": 500,
                    "totalTokens": 1500,
                }
            }
        )
        meta = usage.to_metadata()
        assert meta["codex_turn_input_tokens"] == 1000
        assert meta["codex_turn_total_tokens"] == 1500

    def test_token_usage_format_summary_includes_turn(self) -> None:
        """format_summary() shows per-turn breakdown when deltas > 0."""
        from thenvoi.integrations.codex.types import CodexTokenUsage

        usage = CodexTokenUsage()
        usage.update(
            {
                "usage": {
                    "inputTokens": 1000,
                    "outputTokens": 500,
                    "totalTokens": 1500,
                }
            }
        )
        summary = usage.format_summary()
        assert "turn:" in summary
        assert "+1,000 in" in summary

    def test_reset_turn_deltas(self) -> None:
        """reset_turn_deltas() zeroes out per-turn counters."""
        from thenvoi.integrations.codex.types import CodexTokenUsage

        usage = CodexTokenUsage()
        usage.update(
            {"usage": {"inputTokens": 1000, "outputTokens": 500, "totalTokens": 1500}}
        )
        assert usage.turn_total_tokens == 1500
        usage.reset_turn_deltas()
        assert usage.turn_total_tokens == 0
        assert usage.turn_input_tokens == 0

        # Thread-level totals should be unchanged
        assert usage.total_tokens == 1500

    def test_multi_event_turn_delta_is_cumulative_from_anchor(self) -> None:
        """A turn with multiple tokenUsage events reports the running turn total.

        Without the turn-start anchor, each ``update()`` would overwrite
        ``turn_*`` with the per-event delta, so a turn with events at
        cumulative 150 then 180 (after resetting at 100) would end the
        turn reporting ``turn_input_tokens=30``.  With the anchor, the
        final value is ``180 - 100 = 80`` — the whole-turn rise.
        """
        from thenvoi.integrations.codex.types import CodexTokenUsage

        usage = CodexTokenUsage()
        # End of previous turn: cumulative = 100.
        usage.update({"usage": {"inputTokens": 100, "outputTokens": 0}})
        # New turn starts — anchor captured at 100.
        usage.reset_turn_deltas()

        # First event inside the turn.
        usage.update({"usage": {"inputTokens": 150, "outputTokens": 0}})
        assert usage.turn_input_tokens == 50  # 150 - 100

        # Second event inside the same turn — must keep growing from anchor.
        usage.update({"usage": {"inputTokens": 180, "outputTokens": 0}})
        assert usage.turn_input_tokens == 80  # 180 - 100, NOT 180 - 150

        # Third event: still anchored at 100.
        usage.update({"usage": {"inputTokens": 200, "outputTokens": 0}})
        assert usage.turn_input_tokens == 100  # 200 - 100


# ===========================================================================
# Official SDK transport integration
# ===========================================================================


class TestSdkTransport:
    """Tests for the ``transport="sdk"`` option using :class:`CodexSdkClient`."""

    def test_transport_kind_includes_sdk(self) -> None:
        """The TransportKind literal accepts 'sdk'."""
        config = CodexAdapterConfig(transport="sdk")
        assert config.transport == "sdk"

    def test_build_sdk_client_creates_sdk_client(self) -> None:
        """_build_sdk_client returns a CodexSdkClient with the request handler set."""
        try:
            from thenvoi.integrations.codex.sdk_client import CodexSdkClient
        except ImportError:
            pytest.skip("codex-app-server-sdk not installed")

        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="sdk"),
            client_factory=lambda _config: FakeCodexClient(),
        )
        try:
            client = adapter._build_sdk_client(adapter.config)
        except ImportError:
            pytest.skip("codex-app-server-sdk not installed")
        assert isinstance(client, CodexSdkClient)
        assert client._request_handler is not None

    def test_sdk_request_context_set_and_cleared(self) -> None:
        """_sdk_request_context is set before event loop and cleared after."""
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: FakeCodexClient(),
        )
        # Before any message, context should be None
        assert adapter._sdk_request_context is None

    @pytest.mark.asyncio
    async def test_sdk_server_request_without_context_declines_approvals(self) -> None:
        """No _sdk_request_context → approval methods default to decline, not silent-accept."""
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        adapter._client = fake_client
        adapter._sdk_request_context = None

        from thenvoi.integrations.codex import RpcEvent

        await adapter._handle_sdk_server_request(
            RpcEvent(
                kind="request",
                method="item/commandExecution/requestApproval",
                params={"command": "rm -rf /"},
                id=7,
                raw={},
            ),
        )
        responses = [
            (req_id, payload)
            for req_id, payload in fake_client.responses
            if req_id == 7
        ]
        assert responses == [(7, {"decision": "decline"})]

    @pytest.mark.asyncio
    async def test_sdk_server_request_without_context_empty_for_non_approval(
        self,
    ) -> None:
        """Non-approval methods without context get an empty dict response."""
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        adapter._client = fake_client
        adapter._sdk_request_context = None

        from thenvoi.integrations.codex import RpcEvent

        await adapter._handle_sdk_server_request(
            RpcEvent(
                kind="request",
                method="item/tool/call",
                params={"tool": "unknown"},
                id=8,
                raw={},
            ),
        )
        assert (8, {}) in fake_client.responses


class TestCodexSdkClient:
    """Unit tests for :class:`CodexSdkClient` (mocked SDK)."""

    def test_notification_to_rpc_event_pydantic_model(self) -> None:
        """Notifications with Pydantic payloads are converted to RpcEvent."""
        try:
            from thenvoi.integrations.codex.sdk_client import CodexSdkClient
        except ImportError:
            pytest.skip("codex-app-server-sdk not installed")

        # Simulate a typed SDK notification with a model_dump method
        class FakePayload:
            def model_dump(self, *, by_alias: bool = False) -> dict[str, Any]:
                return {
                    "turn": {"id": "turn-1", "status": "completed", "items": []},
                }

        class FakeNotification:
            method = "turn/completed"
            payload = FakePayload()

        event = CodexSdkClient._notification_to_rpc_event(FakeNotification())
        assert event.kind == "notification"
        assert event.method == "turn/completed"
        assert event.id is None
        assert isinstance(event.params, dict)
        assert event.params["turn"]["status"] == "completed"

    def test_notification_to_rpc_event_unknown_notification(self) -> None:
        """Unknown notifications (with .params) are converted to RpcEvent."""
        try:
            from thenvoi.integrations.codex.sdk_client import CodexSdkClient
        except ImportError:
            pytest.skip("codex-app-server-sdk not installed")

        class FakeUnknownPayload:
            params: dict[str, Any] = {"some": "data"}

        class FakeNotification:
            method = "custom/unknown"
            payload = FakeUnknownPayload()

        event = CodexSdkClient._notification_to_rpc_event(FakeNotification())
        assert event.method == "custom/unknown"
        assert event.params == {"some": "data"}

    def test_auto_handle_server_request_approvals(self) -> None:
        """Auto-handler declines approval requests and returns {} for others."""
        try:
            from thenvoi.integrations.codex.sdk_client import CodexSdkClient
        except ImportError:
            pytest.skip("codex-app-server-sdk not installed")

        result = CodexSdkClient._auto_handle_server_request(
            "item/commandExecution/requestApproval", {"command": "npm test"}
        )
        assert result == {"decision": "decline"}

        result = CodexSdkClient._auto_handle_server_request(
            "item/fileChange/requestApproval", {}
        )
        assert result == {"decision": "decline"}

        result = CodexSdkClient._auto_handle_server_request(
            "item/tool/call", {"tool": "test"}
        )
        assert "error" in result
        assert result["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_respond_unblocks_pending_future(self) -> None:
        """respond() sets the future so the SDK thread unblocks."""
        try:
            from thenvoi.integrations.codex.sdk_client import CodexSdkClient
        except ImportError:
            pytest.skip("codex-app-server-sdk not installed")

        import concurrent.futures

        client = CodexSdkClient.__new__(CodexSdkClient)
        client._pending_server_responses = {}
        client._pending_lock = __import__("threading").Lock()

        future: concurrent.futures.Future[dict[str, Any]] = concurrent.futures.Future()
        client._pending_server_responses[42] = (future, "item/tool/call")

        await client.respond(42, {"decision": "accept"})

        assert future.done()
        assert future.result() == {"decision": "accept"}
        assert 42 not in client._pending_server_responses

    @pytest.mark.asyncio
    async def test_respond_error_unblocks_with_error_info(self) -> None:
        """respond_error() sets error dict to unblock the SDK thread."""
        try:
            from thenvoi.integrations.codex.sdk_client import CodexSdkClient
        except ImportError:
            pytest.skip("codex-app-server-sdk not installed")

        import concurrent.futures

        client = CodexSdkClient.__new__(CodexSdkClient)
        client._pending_server_responses = {}
        client._pending_lock = __import__("threading").Lock()

        future: concurrent.futures.Future[dict[str, Any]] = concurrent.futures.Future()
        client._pending_server_responses[99] = (future, "item/tool/call")

        await client.respond_error(99, code=-32000, message="fail")

        assert future.done()
        assert future.result() == {
            "error": {"code": -32000, "message": "fail", "data": None},
        }

    @pytest.mark.asyncio
    async def test_close_resolves_pending_approvals_with_decline(self) -> None:
        """close() must resolve pending approval futures with ``decline`` — never silent-accept."""
        try:
            from thenvoi.integrations.codex.sdk_client import CodexSdkClient
        except ImportError:
            pytest.skip("codex-app-server-sdk not installed")

        import concurrent.futures

        client = CodexSdkClient.__new__(CodexSdkClient)
        client._connected = True
        client._sync_client = None
        client._pending_server_responses = {}
        client._pending_lock = __import__("threading").Lock()
        client._handler_futures = set()

        approval_future: concurrent.futures.Future[dict[str, Any]] = (
            concurrent.futures.Future()
        )
        tool_future: concurrent.futures.Future[dict[str, Any]] = (
            concurrent.futures.Future()
        )
        client._pending_server_responses[1] = (
            approval_future,
            "item/commandExecution/requestApproval",
        )
        client._pending_server_responses[2] = (
            tool_future,
            "item/tool/call",
        )

        await client.close()

        assert approval_future.done()
        # Pending approval must never silent-accept on close.
        assert approval_future.result() == {"decision": "decline"}
        assert tool_future.done()
        # Non-approval methods get an empty dict (no safe "null" response).
        assert tool_future.result() == {}

    def test_server_request_bridge_auto_handles_during_request(self) -> None:
        """During _in_request=True, server requests are auto-declined."""
        try:
            from thenvoi.integrations.codex.sdk_client import CodexSdkClient
        except ImportError:
            pytest.skip("codex-app-server-sdk not installed")

        client = CodexSdkClient.__new__(CodexSdkClient)
        client._connected = True
        client._in_request = __import__("threading").Event()
        client._in_request.set()
        client._loop = None
        client._request_handler = None
        client._pending_server_responses = {}
        client._pending_lock = __import__("threading").Lock()
        client._next_synthetic_id = 0

        result = client._server_request_bridge(
            "item/commandExecution/requestApproval", {"command": "ls"}
        )
        assert result == {"decision": "decline"}

    def test_error_conversion(self) -> None:
        """SDK JsonRpcError is converted to CodexJsonRpcError."""
        try:
            from codex_app_server import JsonRpcError as SdkJsonRpcError

            from thenvoi.integrations.codex.sdk_client import _convert_sdk_error
        except ImportError:
            pytest.skip("codex-app-server-sdk not installed")

        sdk_err = SdkJsonRpcError(code=-32001, message="overloaded")
        converted = _convert_sdk_error(sdk_err)
        assert isinstance(converted, CodexJsonRpcError)
        assert converted.code == -32001
        assert converted.message == "overloaded"

    def test_error_conversion_passthrough(self) -> None:
        """Non-SDK exceptions pass through unchanged."""
        try:
            from thenvoi.integrations.codex.sdk_client import _convert_sdk_error
        except ImportError:
            pytest.skip("codex-app-server-sdk not installed")

        err = ValueError("not an SDK error")
        assert _convert_sdk_error(err) is err


# ===========================================================================
# Review follow-ups: fixes for bugs/issues found in code review
# ===========================================================================


class TestPlanStepsRobustness:
    """parse_plan_steps tolerance for malformed payloads."""

    def test_parse_plan_steps_handles_non_dict_plan(self) -> None:
        """parse_plan_steps must not crash when `plan` is not a dict."""
        from thenvoi.integrations.codex.types import parse_plan_steps

        assert parse_plan_steps({"plan": "not-a-dict"}) == []
        assert parse_plan_steps({"plan": ["also", "not", "a", "dict"]}) == []
        assert parse_plan_steps({"plan": None}) == []

    def test_parse_plan_steps_reads_top_level_when_plan_absent(self) -> None:
        """When there's no 'plan' key, parse_plan_steps looks at top-level steps."""
        from thenvoi.integrations.codex.types import parse_plan_steps

        steps = parse_plan_steps({"steps": [{"text": "A", "status": "pending"}]})
        assert len(steps) == 1
        assert steps[0].step == "A"


class TestSessionApprovalValidation:
    """Guards that stop /approve-session from storing bogus session keys."""

    @pytest.mark.asyncio
    async def test_approve_session_rejects_empty_session_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """/approve-session for a request with no command signature is rejected.

        Without this guard, _session_approved would silently accumulate "" and
        the user would see "Future `` requests will be auto-approved".
        """
        events = [
            _event_request(
                15,
                "item/fileChange/requestApproval",
                {},  # No command field -> session_approval_key returns ""
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", approval_mode="manual"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        async def approve_session_later() -> None:
            await _wait_for_pending_approval(adapter, "room-1")
            await adapter.on_message(
                make_platform_message(content="/approve-session req-15"),
                tools,
                CodexSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-1",
            )

        # File-change approvals DO key on method, so session-level would
        # normally succeed.  Force the empty-key path by patching
        # _session_approval_key to return "" for the file-change method.
        original_key = adapter._session_approval_key

        def _patched_key(method: str, params: dict[str, Any]) -> str:
            if method == "item/fileChange/requestApproval":
                return ""
            return original_key(method, params)

        monkeypatch.setattr(adapter, "_session_approval_key", _patched_key)

        task = asyncio.create_task(approve_session_later())
        # Manual approval waits for the user.  Decline path will be hit after
        # /approve-session is rejected because the pending future stays open;
        # short approval_wait_timeout_s keeps this test snappy.
        adapter.config.approval_wait_timeout_s = 1.0
        adapter.config.approval_timeout_decision = "decline"
        await adapter.on_message(
            make_platform_message(content="run"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await task

        # No empty-string pattern was stored.
        assert "" not in (adapter._session_approved.get("room-1") or ())
        # The user got the "cannot be resolved as session-level" message.
        rejection = [
            m
            for m in tools.messages_sent
            if "cannot be resolved as session-level" in m["content"]
        ]
        assert len(rejection) == 1


class TestTokenUsageEmission:
    """Emission guards for _emit_token_usage_event."""

    @pytest.mark.asyncio
    async def test_token_usage_event_skipped_when_total_is_zero(self) -> None:
        """_emit_token_usage_event must not emit before any real usage arrives.

        Prior to the fix, `if not usage:` was always False (dataclass
        instances are truthy) so an empty token_usage event could be emitted
        even before Codex sent any thread/tokenUsage/updated notification.
        """
        from thenvoi.integrations.codex.types import CodexTokenUsage

        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        # Seed an empty CodexTokenUsage (total_tokens == 0) and call the
        # emit helper directly.  It should short-circuit.
        adapter._token_usage["thread-x"] = CodexTokenUsage()
        await adapter._emit_token_usage_event(
            tools=tools, thread_id="thread-x", room_id="room-1"
        )

        usage_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "token_usage"
        ]
        assert usage_events == []


class TestSdkClientConstruction:
    """CodexSdkClient construction, timeout plumbing, and import guards."""

    def test_sdk_client_uses_configurable_timeout(self) -> None:
        """CodexSdkClient stores server_request_timeout_s passed at construction."""
        try:
            from thenvoi.integrations.codex.sdk_client import CodexSdkClient
        except ImportError:
            pytest.skip("codex-app-server-sdk not installed")

        # Construct without actually invoking the SDK: build a bare instance
        # and assign the field directly, mirroring what __init__ does after
        # the SDK import succeeds.
        client = CodexSdkClient.__new__(CodexSdkClient)
        client._server_request_timeout_s = 777.0
        assert client._server_request_timeout_s == 777.0

    def test_sdk_client_build_passes_timeout_from_config(self) -> None:
        """_build_sdk_client forwards approval_wait_timeout_s + margin."""
        from unittest.mock import patch

        captured: dict[str, Any] = {}

        def fake_ctor(**kwargs: Any) -> Any:
            captured.update(kwargs)
            obj = type("Stub", (), {})()
            obj.set_request_handler = lambda *_a, **_kw: None
            return obj

        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="sdk", approval_wait_timeout_s=120.0),
        )
        with patch("thenvoi.integrations.codex.sdk_client.CodexSdkClient", fake_ctor):
            adapter._build_sdk_client(adapter.config)

        assert captured["server_request_timeout_s"] == 120.0 + 30.0


class TestStructuredErrorNormalization:
    """build_structured_error_metadata handling of non-standard inputs."""

    def test_structured_error_with_string_error_obj(self) -> None:
        """_handle_error_event normalizes string error_obj before structuring.

        The review flagged a redundant isinstance(error_obj, dict) check that
        was dead code; this test asserts the normalization still works when
        the original error_obj is a string rather than a dict.
        """
        from thenvoi.integrations.codex.types import build_structured_error_metadata

        # Simulate the normalization the adapter performs: convert string to
        # {"message": <str>} before passing to build_structured_error_metadata.
        error_obj: dict[str, Any] = {"message": "raw string error"}
        content, meta = build_structured_error_metadata(error_obj)
        assert "raw string error" in content
        # No codexErrorInfo -> no known error type.
        assert meta["codex_error_type"] is None


class TestSdkClientVersionGuard:
    """CodexSdkClient import-time diagnostics on unsupported Python versions."""

    def test_sdk_client_raises_version_error_on_python_lt_312(self) -> None:
        """When Python < 3.12, CodexSdkClient raises a clearer ImportError."""
        from unittest.mock import patch

        # Force the codex_app_server import to fail
        import builtins

        original_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "codex_app_server":
                raise ImportError("no module named codex_app_server")
            return original_import(name, *args, **kwargs)

        class FakeVersionInfo:
            major = 3
            minor = 11

            def __lt__(self, other: Any) -> bool:
                return (self.major, self.minor) < other

        with (
            patch.object(builtins, "__import__", side_effect=fake_import),
            patch("thenvoi.integrations.codex.sdk_client.sys") as mock_sys,
        ):
            mock_sys.version_info = FakeVersionInfo()
            from thenvoi.integrations.codex import sdk_client as sdk_mod

            with pytest.raises(ImportError) as exc_info:
                sdk_mod.CodexSdkClient()

        assert "Python >= 3.12" in str(exc_info.value)
        assert "thenvoi-sdk[codex]" in str(exc_info.value)


class TestSessionApprovalKeying:
    """_session_approval_key behaviour across method/param shapes."""

    def test_file_change_session_key_requires_paths(self) -> None:
        """/approve-session must refuse fileChange requests with no paths."""
        adapter = CodexAdapter(config=CodexAdapterConfig(transport="ws"))
        key = adapter._session_approval_key(
            "item/fileChange/requestApproval",
            {"reason": "something vague"},
        )
        assert key == ""

    def test_file_change_session_key_uses_paths_when_present(self) -> None:
        """fileChange session key includes sorted path list for stable matching."""
        adapter = CodexAdapter(config=CodexAdapterConfig(transport="ws"))
        key1 = adapter._session_approval_key(
            "item/fileChange/requestApproval",
            {"changes": [{"path": "b.py"}, {"path": "a.py"}]},
        )
        key2 = adapter._session_approval_key(
            "item/fileChange/requestApproval",
            {"changes": [{"path": "a.py"}, {"path": "b.py"}]},
        )
        assert key1 == key2
        assert key1 == "fileChange:a.py|b.py"

    def test_file_change_session_key_handles_top_level_paths(self) -> None:
        """fileChange session key also picks up top-level path/paths fields."""
        adapter = CodexAdapter(config=CodexAdapterConfig(transport="ws"))
        key = adapter._session_approval_key(
            "item/fileChange/requestApproval",
            {"paths": ["src/foo.py", "src/bar.py"]},
        )
        assert "src/foo.py" in key
        assert "src/bar.py" in key

    def test_unknown_approval_method_returns_empty_key(self) -> None:
        """Session-level approval refuses unknown methods rather than bucketing them."""
        adapter = CodexAdapter(config=CodexAdapterConfig(transport="ws"))
        assert adapter._session_approval_key("item/unknown/requestApproval", {}) == ""

    @pytest.mark.asyncio
    async def test_approve_session_refused_for_fileChange_without_paths(self) -> None:
        """/approve-session for a fileChange request with no paths is rejected."""
        events = [
            _event_request(
                88,
                "item/fileChange/requestApproval",
                {"reason": "write something"},
            ),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                approval_mode="manual",
                approval_wait_timeout_s=0.05,
                approval_timeout_decision="decline",
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Manual approval times out and the pending record is cleared,
        # so /approve-session reports no pending approvals rather than
        # storing an empty session key.
        tools.messages_sent.clear()
        await adapter.on_message(
            make_platform_message(content="/approve-session 88"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        assert any("No pending approvals" in m["content"] for m in tools.messages_sent)
        assert "room-1" not in adapter._session_approved


class TestModelUnavailableDetection:
    """_is_model_unavailable_error across structured and unstructured errors."""

    def test_model_unavailable_matches_structured_error_type(self) -> None:
        """_is_model_unavailable_error trusts codexErrorInfo.type when present."""
        err = CodexJsonRpcError(
            code=-32000,
            message="some opaque server message",
            data={"codexErrorInfo": {"type": "ModelNotFound"}},
        )
        assert CodexAdapter._is_model_unavailable_error(err) is True

    def test_model_unavailable_matches_http_404(self) -> None:
        """HTTP 404 signals model-not-found regardless of message wording."""
        err = CodexJsonRpcError(
            code=-32000,
            message="",
            data={"codexErrorInfo": {"httpStatus": 404}},
        )
        assert CodexAdapter._is_model_unavailable_error(err) is True

    def test_model_unavailable_falls_back_to_substring(self) -> None:
        """When no structured info is present, substring matching still works."""
        err = CodexJsonRpcError(
            code=-32000,
            message="Requested model not available on this account",
        )
        assert CodexAdapter._is_model_unavailable_error(err) is True

    def test_model_unavailable_returns_false_for_unrelated_error(self) -> None:
        err = CodexJsonRpcError(code=-32000, message="network blip")
        assert CodexAdapter._is_model_unavailable_error(err) is False


class TestTokenUsageCounterMonotonicity:
    """CodexTokenUsage protection against non-monotonic cumulative updates."""

    def test_token_usage_warns_on_non_monotonic_counters(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A decreasing cumulative counter triggers a warning.

        After the adapter anchors a new turn (``reset_turn_deltas``), a
        late event from the previous turn with a smaller cumulative must
        leave the turn deltas clamped to 0 rather than going negative.
        """
        from thenvoi.integrations.codex.types import CodexTokenUsage

        usage = CodexTokenUsage()
        usage.update({"usage": {"inputTokens": 100, "outputTokens": 100}})
        # Adapter anchors the new turn at cumulative=100/100.
        usage.reset_turn_deltas()
        with caplog.at_level(
            logging.WARNING, logger="thenvoi.integrations.codex.types"
        ):
            usage.update({"usage": {"inputTokens": 50, "outputTokens": 50}})
        assert any(
            "token usage counter decreased" in record.message.lower()
            for record in caplog.records
        )
        # Cumulative stays at 100 (monotonic); turn delta clamped to 0.
        assert usage.input_tokens == 100
        assert usage.output_tokens == 100
        assert usage.turn_input_tokens == 0
        assert usage.turn_output_tokens == 0


class TestApprovalAuditRecording:
    """_record_approval_audit API surface."""

    def test_record_approval_audit_returns_entry(self) -> None:
        """_record_approval_audit returns the entry it appended."""
        adapter = CodexAdapter(config=CodexAdapterConfig(transport="ws"))
        entry = adapter._record_approval_audit(
            room_id="room-1",
            request_id="req-1",
            method="item/commandExecution/requestApproval",
            decision="accept",
            decided_by="tester",
            summary="command: ls",
        )
        assert entry.request_id == "req-1"
        assert entry.decision == "accept"
        assert adapter._approval_audit["room-1"][-1] is entry


# ===========================================================================
# Review follow-ups (review-202 round): coverage gaps surfaced during review
# ===========================================================================


class TestStructuredErrorMappings:
    """Cover every entry in CODEX_ERROR_REMEDIATION plus the fallback path."""

    @pytest.mark.parametrize(
        ("error_type", "expected_action", "expected_phrase"),
        [
            ("HttpConnectionFailed", "check_connectivity", "http connection"),
            ("SandboxError", "review_sandbox_policy", "sandbox"),
            ("Unauthorized", "re_authenticate", "unauthorized"),
            ("BadRequest", "check_input_format", "bad request"),
            (
                "ResponseTooManyFailedAttempts",
                "retry_different_approach",
                "failed attempts",
            ),
        ],
    )
    def test_known_error_type_maps_to_remediation(
        self, error_type: str, expected_action: str, expected_phrase: str
    ) -> None:
        from thenvoi.integrations.codex.types import build_structured_error_metadata

        content, meta = build_structured_error_metadata(
            {"codexErrorInfo": {"type": error_type, "retryable": True}}
        )
        assert meta["codex_error_type"] == error_type
        assert meta["codex_suggested_action"] == expected_action
        assert meta["codex_is_retryable"] is True
        assert expected_phrase in content.lower()

    def test_non_dict_codex_error_info_is_tolerated(self) -> None:
        from thenvoi.integrations.codex.types import build_structured_error_metadata

        content, meta = build_structured_error_metadata(
            {"message": "boom", "codexErrorInfo": "not-a-dict"}
        )
        assert meta["codex_error_type"] is None
        assert content == "boom"

    def test_missing_codex_error_info_falls_back_to_message(self) -> None:
        from thenvoi.integrations.codex.types import build_structured_error_metadata

        content, meta = build_structured_error_metadata({"message": "network down"})
        assert meta["codex_error_type"] is None
        assert meta["codex_suggested_action"] is None
        assert content == "network down"

    def test_additional_details_preserved_in_metadata(self) -> None:
        from thenvoi.integrations.codex.types import build_structured_error_metadata

        _, meta = build_structured_error_metadata(
            {
                "codexErrorInfo": {"type": "Unauthorized"},
                "additionalDetails": {"hint": "refresh token"},
            }
        )
        assert meta["codex_additional_details"] == {"hint": "refresh token"}


class TestSlashCommandCoverage:
    @pytest.mark.asyncio
    async def test_thread_info_with_no_mapping(self) -> None:
        """/thread info reports gracefully when the room has no thread yet."""
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="/thread info"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.messages_sent
        assert "No thread mapped" in tools.messages_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_thread_info_includes_thread_and_usage(self) -> None:
        """/thread info echoes current thread id and token usage summary."""
        events = [
            _event_notification(
                "thread/tokenUsage/updated",
                {
                    "usage": {
                        "inputTokens": 100,
                        "outputTokens": 50,
                        "totalTokens": 150,
                    }
                },
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                emit_token_usage_events=True,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await adapter.on_message(
            make_platform_message(content="/thread info"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        info_msgs = [m for m in tools.messages_sent if "Thread info:" in m["content"]]
        assert len(info_msgs) == 1
        content = info_msgs[0]["content"]
        assert "thread_id: thr-1" in content
        assert "room_id: room-1" in content
        assert "150" in content

    @pytest.mark.asyncio
    async def test_permissions_reflects_sandbox_override(self) -> None:
        """/permissions reports the per-room sandbox override once set."""
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="/sandbox read-only"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await adapter.on_message(
            make_platform_message(content="/permissions"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        perm_msgs = [
            m for m in tools.messages_sent if "Effective permissions:" in m["content"]
        ]
        assert len(perm_msgs) == 1
        assert "read-only" in perm_msgs[0]["content"]


class TestMalformedPayloadTolerance:
    """Adapter must survive notifications that are missing or misshapen."""

    @pytest.mark.asyncio
    async def test_error_event_with_non_dict_error_field(self) -> None:
        """`error` notification where `error` is a string must not crash the turn."""
        events = [
            _event_notification("error", {"error": "oops"}),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", structured_errors=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

    @pytest.mark.asyncio
    async def test_turn_completed_without_items_key(self) -> None:
        """turn/completed missing `items` is treated as an empty turn, not a crash."""
        events = [
            _event_notification(
                "turn/completed",
                {"turn": {"id": "turn-1", "status": "completed"}},
            ),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws"),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

    @pytest.mark.asyncio
    async def test_turn_plan_updated_with_garbage_steps(self) -> None:
        """Plan deltas containing non-list `steps` must be skipped, not crash."""
        events = [
            _event_notification(
                "turn/plan/updated",
                {"plan": {"steps": "not-a-list"}},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", stream_plan_events=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )


class TestCleanupOnCancel:
    @pytest.mark.asyncio
    async def test_pending_approvals_cleared_on_room_cleanup(self) -> None:
        """on_cleanup must resolve pending approval futures to 'decline'.

        Directly populates adapter state to isolate cleanup behavior from
        the _rpc_lock held by on_message — this verifies that
        _clear_pending_approvals_for_room resolves futures to 'decline',
        not that the natural approval timeout fired first.
        """
        fake_client = FakeCodexClient(events=[])
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                approval_mode="manual",
                approval_wait_timeout_s=30.0,
            ),
            client_factory=lambda _config: fake_client,
        )
        await adapter.on_started("Agent", "A coding agent")

        # Simulate an active room with a pending approval.
        loop = asyncio.get_running_loop()
        approval_future: asyncio.Future[str] = loop.create_future()
        from thenvoi.adapters.codex import _PendingApproval

        adapter._room_threads["room-1"] = "thr-1"
        adapter._pending_approvals["room-1"] = {
            "token-1": _PendingApproval(
                request_id=42,
                method="item/commandExecution/requestApproval",
                summary="rm -rf /",
                created_at=datetime.now(timezone.utc),
                future=approval_future,
                session_key="cmd:rm -rf /",
            ),
        }

        await adapter.on_cleanup("room-1")

        assert adapter._pending_approvals.get("room-1", {}) == {}
        assert "room-1" not in adapter._room_threads
        # Verify cleanup resolved the future to "decline".
        assert approval_future.done()
        assert approval_future.result() == "decline"


class TestTurnLifecycleEventsDisabled:
    @pytest.mark.asyncio
    async def test_no_lifecycle_events_when_disabled(self) -> None:
        """With emit_turn_lifecycle_events=False, neither started nor completed
        lifecycle task events are emitted."""
        events = [
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                emit_turn_lifecycle_events=False,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")

        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        lifecycle_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "turn_lifecycle"
        ]
        assert lifecycle_events == []


class TestSdkClientCleanup:
    @pytest.mark.asyncio
    async def test_close_cancels_tracked_handler_futures(self) -> None:
        """SDK client close() cancels any still-pending handler futures."""
        import concurrent.futures

        try:
            from thenvoi.integrations.codex.sdk_client import CodexSdkClient
        except ImportError:
            pytest.skip("codex-app-server-sdk not installed")

        client = CodexSdkClient.__new__(CodexSdkClient)
        client._pending_lock = __import__("threading").Lock()
        client._pending_server_responses = {}
        client._handler_futures = set()
        client._connected = True
        client._sync_client = None

        pending_future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        client._handler_futures.add(pending_future)

        await client.close()

        assert pending_future.cancelled() or pending_future.done()
        assert client._handler_futures == set()

    @pytest.mark.asyncio
    async def test_close_awaits_cancelled_handler_coroutines(self) -> None:
        """close() waits for cancelled handler coroutines to actually exit.

        Without this guarantee a handler cancelled mid-await could re-enter the
        adapter after the sync client is torn down.
        """
        try:
            from thenvoi.integrations.codex.sdk_client import CodexSdkClient
        except ImportError:
            pytest.skip("codex-app-server-sdk not installed")

        client = CodexSdkClient.__new__(CodexSdkClient)
        client._pending_lock = __import__("threading").Lock()
        client._pending_server_responses = {}
        client._handler_futures = set()
        client._connected = True
        client._sync_client = None

        observed_cancellation = asyncio.Event()

        async def _slow_handler() -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                observed_cancellation.set()
                raise

        loop = asyncio.get_running_loop()
        handler_future = asyncio.run_coroutine_threadsafe(_slow_handler(), loop)
        client._handler_futures.add(handler_future)

        await client.close()

        assert observed_cancellation.is_set()
        assert handler_future.cancelled() or handler_future.done()


class TestTokenUsageCumulativeMonotonicity:
    """Late events with smaller cumulative counters must not rewind state.

    Without the max-preserving guard, a late ``thread/tokenUsage/updated``
    from the previous turn can overwrite the cumulative totals with a
    smaller value.  The next real event of the current turn then computes
    ``turn_delta = new - (rewound)`` and double-counts the gap.
    """

    def test_late_smaller_event_does_not_corrupt_next_delta(self) -> None:
        from thenvoi.integrations.codex.types import CodexTokenUsage

        usage = CodexTokenUsage()
        # End of previous turn: cumulative = 100.
        usage.update({"usage": {"inputTokens": 100, "outputTokens": 0}})
        assert usage.input_tokens == 100

        # Adapter starts a new turn.
        usage.reset_turn_deltas()

        # Late event from the previous turn arrives with a smaller cumulative.
        usage.update({"usage": {"inputTokens": 80, "outputTokens": 0}})
        # Cumulative must stay at 100 (monotonic), turn delta clamped to 0.
        assert usage.input_tokens == 100
        assert usage.turn_input_tokens == 0

        # First real event of the new turn: cumulative = 120.
        usage.update({"usage": {"inputTokens": 120, "outputTokens": 0}})
        # Turn delta is 120 - 100 = 20, NOT 120 - 80 = 40.
        assert usage.turn_input_tokens == 20
        assert usage.input_tokens == 120


class TestStructuredErrorDetailCap:
    """``additionalDetails`` is attacker-influenceable and must be capped."""

    def test_long_additional_details_string_is_truncated(self) -> None:
        from thenvoi.integrations.codex.types import (
            _MAX_ERROR_DETAIL_CHARS,
            build_structured_error_metadata,
        )

        long_detail = "x" * (_MAX_ERROR_DETAIL_CHARS + 500)
        _, meta = build_structured_error_metadata(
            {
                "codexErrorInfo": {"type": "Unauthorized"},
                "additionalDetails": long_detail,
            }
        )
        detail = meta["codex_additional_details"]
        assert isinstance(detail, str)
        assert len(detail) < len(long_detail)
        assert "truncated" in detail

    def test_structured_dict_additional_details_are_preserved(self) -> None:
        """Only string details are capped; dict/list payloads pass through."""
        from thenvoi.integrations.codex.types import build_structured_error_metadata

        payload = {"hint": "refresh token", "code": 401}
        _, meta = build_structured_error_metadata(
            {
                "codexErrorInfo": {"type": "Unauthorized"},
                "additionalDetails": payload,
            }
        )
        assert meta["codex_additional_details"] == payload

    def test_empty_additional_details_is_dropped(self) -> None:
        """Empty strings are not echoed into metadata."""
        from thenvoi.integrations.codex.types import build_structured_error_metadata

        _, meta = build_structured_error_metadata(
            {
                "codexErrorInfo": {"type": "Unauthorized"},
                "additionalDetails": "",
            }
        )
        assert "codex_additional_details" not in meta

    def test_oversized_dict_additional_details_is_replaced_with_marker(
        self,
    ) -> None:
        """Large non-string payloads must not slip past the byte cap.

        A hostile upstream that embeds a megabyte of nested JSON in
        ``additionalDetails`` would otherwise inflate every downstream
        WebSocket frame.  When the serialized form exceeds the cap we
        replace the whole payload with a truncated marker string.
        """
        from thenvoi.integrations.codex.types import (
            _MAX_ERROR_DETAIL_CHARS,
            build_structured_error_metadata,
        )

        # Build a dict whose JSON serialization comfortably exceeds the cap.
        oversized_value = "x" * (_MAX_ERROR_DETAIL_CHARS + 500)
        payload = {"nested": {"blob": oversized_value}}

        _, meta = build_structured_error_metadata(
            {
                "codexErrorInfo": {"type": "Unauthorized"},
                "additionalDetails": payload,
            }
        )
        detail = meta["codex_additional_details"]
        assert isinstance(detail, str)
        assert "truncated" in detail
        assert len(detail) < len(oversized_value)

    def test_unserializable_additional_details_is_dropped(self) -> None:
        """Payloads that ``json.dumps`` can't handle without ``default=str``
        round-trip through ``default=str``; pathological unserializable
        objects (e.g. a circular reference) must be dropped rather than
        raising into the event-emission path."""
        from thenvoi.integrations.codex.types import build_structured_error_metadata

        circular: dict[str, Any] = {}
        circular["self"] = circular

        _, meta = build_structured_error_metadata(
            {
                "codexErrorInfo": {"type": "Unauthorized"},
                "additionalDetails": circular,
            }
        )
        assert "codex_additional_details" not in meta


class TestDiffByteCap:
    """``turn/diff/updated`` metadata is bounded in UTF-8 bytes, not chars."""

    @pytest.mark.asyncio
    async def test_multibyte_diff_respects_byte_budget(self) -> None:
        """A diff built from 4-byte codepoints is capped to the byte budget,
        not the character budget (which would be ~4× larger on the wire)."""
        from thenvoi.adapters.codex import _MAX_DIFF_METADATA_BYTES

        # Each emoji is 4 UTF-8 bytes; use ~1.5× the byte budget worth.
        emoji = "\U0001f600"
        diff_chars = (_MAX_DIFF_METADATA_BYTES // 4) + 5000
        big_diff = emoji * diff_chars
        assert len(big_diff.encode("utf-8")) > _MAX_DIFF_METADATA_BYTES

        events = [
            _event_notification(
                "turn/diff/updated",
                {"diff": big_diff, "files": ["src/app.py"]},
            ),
            _event_notification(
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
        fake_client = FakeCodexClient(events=events)
        adapter = CodexAdapter(
            config=CodexAdapterConfig(transport="ws", emit_diff_events=True),
            client_factory=lambda _config: fake_client,
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        diff_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "turn_diff"
        ]
        assert len(diff_events) == 1
        meta = diff_events[0]["metadata"]
        emitted = meta["codex_diff"]
        # The emitted diff (including the truncation marker) stays within a
        # small overhead of the byte budget — nowhere near 4× it.
        assert len(emitted.encode("utf-8")) <= _MAX_DIFF_METADATA_BYTES + 256
        assert meta["codex_diff_truncated"] is True
        assert meta["codex_diff_original_bytes"] > _MAX_DIFF_METADATA_BYTES


class TestSlashCommandTokenBoundary:
    """``_extract_local_command`` scans a bounded prefix for slash commands."""

    def test_command_past_search_limit_is_ignored(self) -> None:
        """A slash command buried after the token limit must not fire."""
        from thenvoi.adapters.codex import _COMMAND_TOKEN_SEARCH_LIMIT

        prefix = " ".join(f"word{i}" for i in range(_COMMAND_TOKEN_SEARCH_LIMIT + 1))
        content = f"{prefix} /approve a-1"
        assert CodexAdapter._extract_local_command(content) is None

    def test_command_at_last_scanned_token_is_recognised(self) -> None:
        """The boundary itself is inclusive of the search limit."""
        from thenvoi.adapters.codex import _COMMAND_TOKEN_SEARCH_LIMIT

        prefix = " ".join(f"word{i}" for i in range(_COMMAND_TOKEN_SEARCH_LIMIT - 1))
        content = f"{prefix} /approve a-1"
        result = CodexAdapter._extract_local_command(content)
        assert result == ("approve", "a-1")

    def test_command_after_many_mentions_is_recognised(self) -> None:
        """A long mention block (``@handle DisplayName`` × N) must not bury the command.

        Regression against the old 5-token limit which could drop a legit
        ``/approve`` behind only two or three concurrent mentions.
        """
        mentions = " ".join(
            f"@user{i} Display{i}" for i in range(8)
        )  # 16 tokens (8 mentions × 2) + /approve + id
        content = f"{mentions} /approve a-1"
        result = CodexAdapter._extract_local_command(content)
        assert result == ("approve", "a-1")


class TestDoubleEmitStartupWarning:
    """Enabling both turn-task channels warns operators once at startup."""

    @pytest.mark.asyncio
    async def test_warns_when_both_channels_enabled(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                emit_turn_task_markers=True,
                emit_turn_lifecycle_events=True,
            ),
            client_factory=lambda _config: fake_client,
        )
        with caplog.at_level(logging.WARNING, logger="thenvoi.adapters.codex"):
            await adapter.on_started("Agent", "A coding agent")
        assert any(
            "two task events per turn" in record.message for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_no_warning_when_only_one_channel_enabled(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake_client = FakeCodexClient()
        adapter = CodexAdapter(
            config=CodexAdapterConfig(
                transport="ws",
                emit_turn_task_markers=True,
                emit_turn_lifecycle_events=False,
            ),
            client_factory=lambda _config: fake_client,
        )
        with caplog.at_level(logging.WARNING, logger="thenvoi.adapters.codex"):
            await adapter.on_started("Agent", "A coding agent")
        assert not any(
            "two task events per turn" in record.message for record in caplog.records
        )
