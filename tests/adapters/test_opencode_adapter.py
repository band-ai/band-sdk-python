"""Tests for OpencodeAdapter."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel

from thenvoi.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.core.types import PlatformMessage
from thenvoi.integrations.opencode.types import OpencodeSessionState
from thenvoi.testing import FakeAgentTools


def make_platform_message(
    room_id: str = "room-1",
    content: str = "hello",
    sender_id: str = "user-1",
    sender_name: str = "Alice",
) -> PlatformMessage:
    return PlatformMessage(
        id=str(uuid4()),
        room_id=room_id,
        content=content,
        sender_id=sender_id,
        sender_type="User",
        sender_name=sender_name,
        message_type="text",
        metadata={},
        created_at=datetime.now(),
    )


def event_message_updated(session_id: str, message_id: str) -> dict[str, Any]:
    return {
        "type": "message.updated",
        "properties": {
            "info": {
                "id": message_id,
                "sessionID": session_id,
                "role": "assistant",
            }
        },
    }


def event_text_part(session_id: str, message_id: str, text: str) -> dict[str, Any]:
    return {
        "type": "message.part.updated",
        "properties": {
            "part": {
                "id": f"part-{message_id}",
                "sessionID": session_id,
                "messageID": message_id,
                "type": "text",
                "text": text,
            }
        },
    }


def event_reasoning_part(
    session_id: str, message_id: str, part_id: str = "reasoning-part"
) -> dict[str, Any]:
    return {
        "type": "message.part.updated",
        "properties": {
            "part": {
                "id": part_id,
                "sessionID": session_id,
                "messageID": message_id,
                "type": "reasoning",
                "text": "",
            }
        },
    }


def event_part_delta(
    session_id: str, message_id: str, part_id: str, delta: str
) -> dict[str, Any]:
    return {
        "type": "message.part.delta",
        "properties": {
            "sessionID": session_id,
            "messageID": message_id,
            "partID": part_id,
            "field": "text",
            "delta": delta,
        },
    }


def event_user_message_updated(session_id: str, message_id: str) -> dict[str, Any]:
    return {
        "type": "message.updated",
        "properties": {
            "info": {
                "id": message_id,
                "sessionID": session_id,
                "role": "user",
            }
        },
    }


def event_tool_part(
    session_id: str,
    message_id: str,
    *,
    tool: str,
    call_id: str,
    status: str,
    input_data: dict[str, Any],
    output: Any = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {"status": status, "input": input_data}
    if status == "running":
        state["time"] = {"start": 1}
    if status == "completed":
        state["output"] = "" if output is None else output
        state["title"] = tool
        state["metadata"] = {}
        state["time"] = {"start": 1, "end": 2}

    return {
        "type": "message.part.updated",
        "properties": {
            "part": {
                "id": f"tool-{call_id}-{status}",
                "sessionID": session_id,
                "messageID": message_id,
                "type": "tool",
                "tool": tool,
                "callID": call_id,
                "state": state,
            }
        },
    }


def event_permission(session_id: str, request_id: str) -> dict[str, Any]:
    return {
        "type": "permission.asked",
        "properties": {
            "id": request_id,
            "sessionID": session_id,
            "permission": "bash",
            "patterns": ["rm -rf tmp"],
        },
    }


def event_question(session_id: str, request_id: str, *questions: str) -> dict[str, Any]:
    return {
        "type": "question.asked",
        "properties": {
            "id": request_id,
            "sessionID": session_id,
            "questions": [{"question": question} for question in questions],
        },
    }


def event_session_idle(session_id: str) -> dict[str, Any]:
    return {"type": "session.idle", "properties": {"sessionID": session_id}}


def event_session_error(session_id: str, message: str) -> dict[str, Any]:
    return {
        "type": "session.error",
        "properties": {
            "sessionID": session_id,
            "error": {"name": "APIError", "data": {"message": message}},
        },
    }


def tools_protocol(tools: FakeAgentTools) -> AgentToolsProtocol:
    return cast(AgentToolsProtocol, tools)


class FakeOpencodeClient:
    def __init__(
        self,
        *,
        prompt_event_sequences: list[list[dict[str, Any]]] | None = None,
        reply_permission_events: dict[str, list[dict[str, Any]]] | None = None,
        reply_question_events: dict[str, list[dict[str, Any]]] | None = None,
        reject_question_events: dict[str, list[dict[str, Any]]] | None = None,
        get_session_missing: set[str] | None = None,
        prompt_exceptions: list[Exception] | None = None,
    ) -> None:
        self.created_sessions: list[dict[str, Any]] = []
        self.prompt_calls: list[dict[str, Any]] = []
        self.permission_replies: list[dict[str, Any]] = []
        self.question_replies: list[dict[str, Any]] = []
        self.question_rejections: list[str] = []
        self.aborted_sessions: list[str] = []
        self.registered_mcp_servers: list[dict[str, str]] = []
        self.deregistered_mcp_servers: list[str] = []
        self.closed = False
        self._session_counter = 0
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._prompt_event_sequences = list(prompt_event_sequences or [])
        self._reply_permission_events = reply_permission_events or {}
        self._reply_question_events = reply_question_events or {}
        self._reject_question_events = reject_question_events or {}
        self._get_session_missing = get_session_missing or set()
        self._prompt_exceptions = list(prompt_exceptions or [])

    async def create_session(
        self,
        *,
        title: str | None = None,
    ) -> dict[str, Any]:
        self._session_counter += 1
        session = {
            "id": f"sess-{self._session_counter}",
            "title": title or "",
        }
        self.created_sessions.append(session)
        return session

    async def get_session(self, session_id: str) -> dict[str, Any]:
        if session_id in self._get_session_missing:
            request = AnyHTTPStatusError(404, session_id)
            raise request
        return {"id": session_id, "title": "existing"}

    async def prompt_async(
        self,
        session_id: str,
        *,
        parts: list[dict[str, Any]],
        system: str | None = None,
        model: dict[str, str] | None = None,
        agent: str | None = None,
        variant: str | None = None,
    ) -> None:
        self.prompt_calls.append(
            {
                "session_id": session_id,
                "parts": parts,
                "system": system,
                "model": model,
                "agent": agent,
                "variant": variant,
            }
        )
        if self._prompt_exceptions:
            raise self._prompt_exceptions.pop(0)
        if self._prompt_event_sequences:
            for event in self._prompt_event_sequences.pop(0):
                await self._queue.put(event)

    async def reply_permission(
        self,
        session_id: str,
        permission_id: str,
        *,
        response: str,
    ) -> None:
        self.permission_replies.append(
            {
                "session_id": session_id,
                "permission_id": permission_id,
                "response": response,
            }
        )
        for event in self._reply_permission_events.get(permission_id, []):
            await self._queue.put(event)

    async def reply_question(
        self, request_id: str, *, answers: list[list[str]]
    ) -> None:
        self.question_replies.append({"request_id": request_id, "answers": answers})
        for event in self._reply_question_events.get(request_id, []):
            await self._queue.put(event)

    async def reject_question(self, request_id: str) -> None:
        self.question_rejections.append(request_id)
        for event in self._reject_question_events.get(request_id, []):
            await self._queue.put(event)

    async def abort_session(self, session_id: str) -> None:
        self.aborted_sessions.append(session_id)

    async def register_mcp_server(self, *, name: str, url: str) -> dict[str, Any]:
        self.registered_mcp_servers.append({"name": name, "url": url})
        return {"name": name, "url": url}

    async def deregister_mcp_server(self, name: str) -> None:
        self.deregistered_mcp_servers.append(name)

    async def iter_events(self):
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        self.closed = True
        await self._queue.put(None)


class AnyHTTPStatusError(httpx.HTTPStatusError):
    def __init__(self, status_code: int, session_id: str) -> None:
        request = httpx.Request("GET", f"http://localhost/session/{session_id}")
        response = httpx.Response(status_code=status_code, request=request)
        super().__init__("status error", request=request, response=response)


class FakeMCPBackend:
    """Fake BandMCPBackend for tests."""

    def __init__(
        self,
        *,
        sse_url: str = "http://127.0.0.1:50000/sse",
        stop_started: asyncio.Event | None = None,
        stop_release: asyncio.Event | None = None,
    ) -> None:
        self.kind = "sse"
        self.server = None
        self.allowed_tools: list[str] = []
        self._sse_url = sse_url
        self.local_server = type(
            "_FakeLocalServer", (), {"sse_url": sse_url, "stop": AsyncMock()}
        )()
        self.stop_calls = 0
        self._stop_started = stop_started
        self._stop_release = stop_release

    async def stop(self) -> None:
        self.stop_calls += 1
        if self._stop_started is not None:
            self._stop_started.set()
        if self._stop_release is not None:
            await self._stop_release.wait()


def _make_fake_mcp_backend_factory(
    backend: FakeMCPBackend | None = None,
) -> AsyncMock:
    """Return an AsyncMock that produces a FakeMCPBackend."""
    fake = backend or FakeMCPBackend()

    async def factory(**kwargs: Any) -> FakeMCPBackend:
        return fake

    mock = AsyncMock(side_effect=factory)
    return mock


async def wait_for(predicate, timeout_s: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    pytest.fail("Timed out waiting for condition")


class TestOpencodeAdapter:
    @pytest.fixture(autouse=True)
    def _patch_mcp_backend(self) -> Any:
        """Patch MCP backend creation for all tests by default."""
        with patch(
            "thenvoi.adapters.opencode.create_thenvoi_mcp_backend",
            _make_fake_mcp_backend_factory(),
        ):
            yield

    @pytest.mark.asyncio
    async def test_registers_shared_mcp_backend_with_additional_tools(self) -> None:
        class EchoInput(BaseModel):
            """Echo text."""

            text: str

        def echo_tool(input_data: EchoInput) -> str:
            return input_data.text

        fake_backend = FakeMCPBackend(sse_url="http://127.0.0.1:50000/sse")
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-1"),
                    event_text_part("sess-1", "msg-1", "hello"),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = OpencodeAdapter(
            additional_tools=[(EchoInput, echo_tool)],
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        with patch(
            "thenvoi.adapters.opencode.create_thenvoi_mcp_backend",
            _make_fake_mcp_backend_factory(fake_backend),
        ):
            await adapter.on_started("OpenCode Agent", "A coding agent")
            await adapter.on_message(
                make_platform_message(),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        assert fake_client.registered_mcp_servers == [
            {"name": "thenvoi", "url": "http://127.0.0.1:50000/sse"},
        ]

        await adapter.on_cleanup("room-1")

    @pytest.mark.asyncio
    async def test_registers_shared_mcp_backend_on_startup(self) -> None:
        fake_backend = FakeMCPBackend()
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-1"),
                    event_text_part("sess-1", "msg-1", "hello"),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = OpencodeAdapter(
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        with patch(
            "thenvoi.adapters.opencode.create_thenvoi_mcp_backend",
            _make_fake_mcp_backend_factory(fake_backend),
        ):
            await adapter.on_started("OpenCode Agent", "A coding agent")
            await adapter.on_message(
                make_platform_message(),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        assert fake_client.registered_mcp_servers == [
            {"name": "thenvoi", "url": "http://127.0.0.1:50000/sse"}
        ]

        await adapter.on_cleanup("room-1")
        assert fake_client.deregistered_mcp_servers == ["thenvoi"]
        assert fake_backend.stop_calls == 1

    @pytest.mark.asyncio
    async def test_bootstrap_creates_session_relays_text_and_persists_task(
        self,
    ) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-1"),
                    event_text_part("sess-1", "msg-1", "OpenCode says hi"),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert fake_client.created_sessions[0]["id"] == "sess-1"
        assert tools.messages_sent[0]["content"] == "OpenCode says hi"
        assert tools.messages_sent[0]["mentions"] == [{"id": "user-1"}]
        task_events = [e for e in tools.events_sent if e["message_type"] == "task"]
        assert task_events
        assert task_events[0]["metadata"]["opencode_session_id"] == "sess-1"

    @pytest.mark.asyncio
    async def test_reuses_persisted_session(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-existing", "msg-2"),
                    event_text_part("sess-existing", "msg-2", "Reused session"),
                    event_session_idle("sess-existing"),
                ]
            ]
        )
        adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(session_id="sess-existing", room_id="room-1"),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert fake_client.created_sessions == []
        assert fake_client.prompt_calls[0]["session_id"] == "sess-existing"
        assert tools.messages_sent[0]["content"] == "Reused session"

    @pytest.mark.asyncio
    async def test_manual_permission_reply_from_follow_up_message(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[[event_permission("sess-1", "req-1")]],
            reply_permission_events={
                "req-1": [
                    event_message_updated("sess-1", "msg-3"),
                    event_text_part("sess-1", "msg-3", "Approved and done"),
                    event_session_idle("sess-1"),
                ]
            },
        )
        adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        first_turn = asyncio.create_task(
            adapter.on_message(
                make_platform_message(content="Please continue"),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )

        await wait_for(
            lambda: any(
                "approval requested" in m["content"].lower()
                for m in tools.messages_sent
            )
        )
        await wait_for(lambda: first_turn.done())
        assert all(msg["content"] != "Approved and done" for msg in tools.messages_sent)

        await adapter.on_message(
            make_platform_message(content="approve req-1"),
            tools_protocol(tools),
            OpencodeSessionState(session_id="sess-1", room_id="room-1"),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        await first_turn
        await wait_for(
            lambda: any(
                msg["content"] == "Approved and done" for msg in tools.messages_sent
            )
        )

        assert fake_client.permission_replies == [
            {"session_id": "sess-1", "permission_id": "req-1", "response": "once"}
        ]
        assert any(msg["content"] == "Approved and done" for msg in tools.messages_sent)

    @pytest.mark.asyncio
    async def test_manual_question_reply_from_follow_up_message(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_question("sess-1", "q-1", "What should I do next?")]
            ],
            reply_question_events={
                "q-1": [
                    event_message_updated("sess-1", "msg-4"),
                    event_text_part("sess-1", "msg-4", "Question answered"),
                    event_session_idle("sess-1"),
                ]
            },
        )
        adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        first_turn = asyncio.create_task(
            adapter.on_message(
                make_platform_message(content="Need an answer"),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )

        await wait_for(
            lambda: any(
                "asked question" in message["content"].lower()
                for message in tools.messages_sent
            )
        )
        await wait_for(lambda: first_turn.done())

        await adapter.on_message(
            make_platform_message(content="Ship the adapter"),
            tools_protocol(tools),
            OpencodeSessionState(session_id="sess-1", room_id="room-1"),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        await wait_for(
            lambda: any(
                message["content"] == "Question answered"
                for message in tools.messages_sent
            )
        )
        assert fake_client.question_replies == [
            {"request_id": "q-1", "answers": [["Ship the adapter"]]}
        ]

    @pytest.mark.asyncio
    async def test_prompt_submission_failure_does_not_leave_room_stuck(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-5"),
                    event_text_part("sess-1", "msg-5", "Recovered after failure"),
                    event_session_idle("sess-1"),
                ]
            ],
            prompt_exceptions=[AnyHTTPStatusError(500, "sess-1")],
        )
        adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="first try"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        await adapter.on_message(
            make_platform_message(content="second try"),
            tools_protocol(tools),
            OpencodeSessionState(session_id="sess-1", room_id="room-1"),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert len(fake_client.prompt_calls) == 2
        assert not any(
            "still processing the previous request" in event["content"].lower()
            for event in tools.events_sent
        )
        assert any(
            message["content"] == "Recovered after failure"
            for message in tools.messages_sent
        )

    @pytest.mark.asyncio
    async def test_missing_session_replays_history_into_new_prompt(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-6"),
                    event_text_part("sess-1", "msg-6", "Session recreated"),
                    event_session_idle("sess-1"),
                ]
            ],
            get_session_missing={"sess-missing"},
        )
        adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="Continue from before"),
            tools_protocol(tools),
            OpencodeSessionState(
                session_id="sess-missing",
                room_id="room-1",
                replay_messages=[
                    "[Alice]: Earlier question",
                    "[OpenCode Agent]: Earlier answer",
                ],
            ),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        prompt_text = fake_client.prompt_calls[0]["parts"][0]["text"]
        assert fake_client.created_sessions[0]["id"] == "sess-1"
        assert "Recovered room history" in prompt_text
        assert "[Alice]: Earlier question" in prompt_text
        assert "[OpenCode Agent]: Earlier answer" in prompt_text

    @pytest.mark.asyncio
    async def test_reports_tool_events_when_enabled(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_tool_part(
                        "sess-1",
                        "msg-4",
                        tool="bash",
                        call_id="call-1",
                        status="running",
                        input_data={"command": "pytest"},
                    ),
                    event_tool_part(
                        "sess-1",
                        "msg-4",
                        tool="bash",
                        call_id="call-1",
                        status="completed",
                        input_data={"command": "pytest"},
                        output="ok",
                    ),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(enable_execution_reporting=True),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_calls = [e for e in tools.events_sent if e["message_type"] == "tool_call"]
        tool_results = [
            e for e in tools.events_sent if e["message_type"] == "tool_result"
        ]
        assert len(tool_calls) == 1
        assert len(tool_results) == 1
        assert json.loads(tool_calls[0]["content"])["name"] == "bash"
        assert json.loads(tool_results[0]["content"])["output"] == "ok"

    @pytest.mark.asyncio
    async def test_preserves_falsy_tool_result_outputs_when_reporting(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_tool_part(
                        "sess-1",
                        "msg-7",
                        tool="bash",
                        call_id="call-2",
                        status="completed",
                        input_data={"command": "printf 0"},
                        output=0,
                    ),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(enable_execution_reporting=True),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_results = [
            e for e in tools.events_sent if e["message_type"] == "tool_result"
        ]
        assert len(tool_results) == 1
        assert json.loads(tool_results[0]["content"])["output"] == 0

    @pytest.mark.asyncio
    async def test_does_not_echo_user_text_parts_as_assistant_output(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_user_message_updated("sess-1", "msg-user"),
                    event_text_part("sess-1", "msg-user", "user prompt text"),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(provider_id="openai", model_id="gpt-5.2"),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="user prompt text"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.messages_sent[0]["content"] == (
            "OpenCode completed the turn without a text reply."
        )

    @pytest.mark.asyncio
    async def test_ignores_reasoning_deltas_and_relays_final_text_only(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-assistant"),
                    event_reasoning_part(
                        "sess-1",
                        "msg-assistant",
                        part_id="part-reasoning",
                    ),
                    event_part_delta(
                        "sess-1",
                        "msg-assistant",
                        "part-reasoning",
                        'The user wants "pong".',
                    ),
                    event_text_part("sess-1", "msg-assistant", ""),
                    event_part_delta(
                        "sess-1",
                        "msg-assistant",
                        "part-msg-assistant",
                        "pong",
                    ),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(content="Reply with exactly: pong"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.messages_sent[0]["content"] == "pong"

    @pytest.mark.asyncio
    async def test_session_error_emits_error_event(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[[event_session_error("sess-1", "boom")]]
        )
        adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert error_events
        assert "boom" in error_events[0]["content"].lower()

    @pytest.mark.asyncio
    async def test_cleanup_is_idempotent(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-5"),
                    event_text_part("sess-1", "msg-5", "done"),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        await adapter.on_cleanup("room-1")
        await adapter.on_cleanup("room-1")
        assert fake_client.closed is True

    @pytest.mark.asyncio
    async def test_cleanup_race_creates_a_fresh_client_for_the_next_room(self) -> None:
        stop_started = asyncio.Event()
        stop_release = asyncio.Event()
        fake_backend = FakeMCPBackend(
            stop_started=stop_started,
            stop_release=stop_release,
        )
        first_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-1"),
                    event_text_part("sess-1", "msg-1", "first"),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        second_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-2"),
                    event_text_part("sess-1", "msg-2", "second"),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        clients = [first_client, second_client]
        adapter = OpencodeAdapter(
            client_factory=lambda _config: clients.pop(0),
        )
        tools = FakeAgentTools()

        with patch(
            "thenvoi.adapters.opencode.create_thenvoi_mcp_backend",
            _make_fake_mcp_backend_factory(fake_backend),
        ):
            await adapter.on_started("OpenCode Agent", "A coding agent")
            await adapter.on_message(
                make_platform_message(room_id="room-1"),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

            cleanup_task = asyncio.create_task(adapter.on_cleanup("room-1"))
            await wait_for(stop_started.is_set)

            await adapter.on_message(
                make_platform_message(room_id="room-2", content="next room"),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-2",
            )

            stop_release.set()
            await cleanup_task

        assert len(first_client.prompt_calls) == 1
        assert len(second_client.prompt_calls) == 1
        assert second_client.closed is False

        await adapter.on_cleanup("room-2")
        assert second_client.closed is True

    @pytest.mark.asyncio
    async def test_auto_accept_approval_mode(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_permission("sess-1", "perm-1")],
            ],
            reply_permission_events={
                "perm-1": [
                    event_message_updated("sess-1", "msg-auto"),
                    event_text_part("sess-1", "msg-auto", "auto accepted"),
                    event_session_idle("sess-1"),
                ]
            },
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(approval_mode="auto_accept"),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert fake_client.permission_replies == [
            {"session_id": "sess-1", "permission_id": "perm-1", "response": "once"}
        ]
        # No approval prompt sent to user in auto_accept mode
        assert not any(
            "approval requested" in m["content"].lower() for m in tools.messages_sent
        )

    @pytest.mark.asyncio
    async def test_auto_decline_approval_mode(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_permission("sess-1", "perm-1")],
            ],
            reply_permission_events={"perm-1": [event_session_idle("sess-1")]},
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(approval_mode="auto_decline"),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert fake_client.permission_replies == [
            {"session_id": "sess-1", "permission_id": "perm-1", "response": "reject"}
        ]

    @pytest.mark.asyncio
    async def test_auto_reject_question_mode(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[[event_question("sess-1", "q-1", "What to do?")]],
            reject_question_events={"q-1": [event_session_idle("sess-1")]},
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(question_mode="auto_reject"),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert fake_client.question_rejections == ["q-1"]
        assert not any(
            "asked question" in m["content"].lower() for m in tools.messages_sent
        )

    @pytest.mark.asyncio
    async def test_permission_timeout_expiry(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[[event_permission("sess-1", "perm-timeout")]],
            reply_permission_events={"perm-timeout": [event_session_idle("sess-1")]},
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(
                approval_mode="manual",
                approval_wait_timeout_s=0.1,
                approval_timeout_reply="reject",
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        await wait_for(lambda: len(fake_client.permission_replies) > 0, timeout_s=3.0)
        assert fake_client.permission_replies[0]["response"] == "reject"
        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert any("timed out" in e["content"].lower() for e in error_events)

        await adapter.on_cleanup("room-1")

    @pytest.mark.asyncio
    async def test_question_timeout_expiry(self) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_question("sess-1", "q-timeout", "Pick a color")]
            ],
            reject_question_events={"q-timeout": [event_session_idle("sess-1")]},
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(
                question_mode="manual",
                question_wait_timeout_s=0.1,
            ),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        await wait_for(lambda: len(fake_client.question_rejections) > 0, timeout_s=3.0)
        assert fake_client.question_rejections == ["q-timeout"]
        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert any("timed out" in e["content"].lower() for e in error_events)

        await adapter.on_cleanup("room-1")

    @pytest.mark.asyncio
    async def test_concurrent_message_rejected(self) -> None:
        """Sending a second message while a turn is active returns an error."""
        # First prompt never completes (no session.idle event)
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_message_updated("sess-1", "msg-long")],
                [],  # second prompt gets empty events
            ]
        )
        adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        first_task = asyncio.create_task(
            adapter.on_message(
                make_platform_message(content="first"),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )
        # Wait for first turn to start
        await wait_for(lambda: len(fake_client.prompt_calls) > 0)

        # Send second message while first is active
        await adapter.on_message(
            make_platform_message(content="second"),
            tools_protocol(tools),
            OpencodeSessionState(session_id="sess-1", room_id="room-1"),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        # Second message should get rejected with "still processing" error
        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert any("still processing" in e["content"].lower() for e in error_events)
        assert len(fake_client.prompt_calls) == 1

        # Clean up: cancel the first task
        first_task.cancel()
        try:
            await first_task
        except asyncio.CancelledError:
            pass
        await adapter.on_cleanup("room-1")

    @pytest.mark.asyncio
    async def test_cleanup_with_pending_permission(self) -> None:
        """Cleanup mid-permission cancels timeout without crash."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[[event_permission("sess-1", "perm-cleanup")]],
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(approval_mode="manual"),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        task = asyncio.create_task(
            adapter.on_message(
                make_platform_message(),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )

        await wait_for(
            lambda: any(
                "approval requested" in m["content"].lower()
                for m in tools.messages_sent
            )
        )

        # Cleanup while permission is pending
        await adapter.on_cleanup("room-1")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # No permission reply should have been sent (just cleaned up)
        assert fake_client.permission_replies == []

    @pytest.mark.asyncio
    async def test_cleanup_with_pending_question(self) -> None:
        """Cleanup mid-question cancels timeout without crash."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_question("sess-1", "q-cleanup", "Something?")]
            ],
        )
        adapter = OpencodeAdapter(
            config=OpencodeAdapterConfig(question_mode="manual"),
            client_factory=lambda _config: fake_client,
        )
        tools = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")
        task = asyncio.create_task(
            adapter.on_message(
                make_platform_message(),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )

        await wait_for(
            lambda: any(
                "asked question" in m["content"].lower() for m in tools.messages_sent
            )
        )

        # Cleanup while question is pending
        await adapter.on_cleanup("room-1")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # No question reply should have been sent
        assert fake_client.question_replies == []
        assert fake_client.question_rejections == []

    @pytest.mark.asyncio
    async def test_two_rooms_active_concurrently(self) -> None:
        """Two rooms with separate sessions route events correctly."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                # Room 1 prompt events
                [
                    event_message_updated("sess-1", "msg-r1"),
                    event_text_part("sess-1", "msg-r1", "reply to room 1"),
                    event_session_idle("sess-1"),
                ],
                # Room 2 prompt events
                [
                    event_message_updated("sess-2", "msg-r2"),
                    event_text_part("sess-2", "msg-r2", "reply to room 2"),
                    event_session_idle("sess-2"),
                ],
            ]
        )
        adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)
        tools_r1 = FakeAgentTools()
        tools_r2 = FakeAgentTools()

        await adapter.on_started("OpenCode Agent", "A coding agent")

        # Start room 1
        await adapter.on_message(
            make_platform_message(room_id="room-1", content="hello room 1"),
            tools_protocol(tools_r1),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        # Start room 2 (shared client, different session)
        await adapter.on_message(
            make_platform_message(room_id="room-2", content="hello room 2"),
            tools_protocol(tools_r2),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-2",
        )

        # Each room got its own session
        assert len(fake_client.created_sessions) == 2
        assert fake_client.created_sessions[0]["id"] == "sess-1"
        assert fake_client.created_sessions[1]["id"] == "sess-2"

        # Each room received the correct reply
        assert any("reply to room 1" in m["content"] for m in tools_r1.messages_sent)
        assert any("reply to room 2" in m["content"] for m in tools_r2.messages_sent)

        # Cleanup room 1 while room 2 state is still tracked
        await adapter.on_cleanup("room-1")
        # Client should still be alive (room 2 exists)
        assert not fake_client.closed

        # Cleanup room 2 shuts down the client
        await adapter.on_cleanup("room-2")
        assert fake_client.closed
