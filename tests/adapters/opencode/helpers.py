"""Shared OpenCode adapter test fakes and builders."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, TypeAlias, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from band.adapters.opencode import OpencodeAdapter
from band.core.exceptions import BandToolError
from band.core.protocols import AgentToolsProtocol
from band.core.types import (
    PlatformMessage,
)
from band.integrations.opencode.types import OpencodeSessionState
from band.testing import FakeAgentTools

RawOpencodeEvent: TypeAlias = dict[str, Any]


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


def event_message_updated(session_id: str, message_id: str) -> RawOpencodeEvent:
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


def event_text_part(session_id: str, message_id: str, text: str) -> RawOpencodeEvent:
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
) -> RawOpencodeEvent:
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
) -> RawOpencodeEvent:
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


def event_message_updated_with_tokens(
    session_id: str, message_id: str, tokens: dict[str, Any]
) -> RawOpencodeEvent:
    return {
        "type": "message.updated",
        "properties": {
            "info": {
                "id": message_id,
                "sessionID": session_id,
                "role": "assistant",
                "tokens": tokens,
            }
        },
    }


def event_user_message_updated(session_id: str, message_id: str) -> RawOpencodeEvent:
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
) -> RawOpencodeEvent:
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


def event_permission(
    session_id: str, request_id: str, *, permission: str = "bash"
) -> RawOpencodeEvent:
    return {
        "type": "permission.asked",
        "properties": {
            "id": request_id,
            "sessionID": session_id,
            "permission": permission,
            "patterns": ["rm -rf tmp"],
        },
    }


def event_question(
    session_id: str, request_id: str, *questions: str
) -> RawOpencodeEvent:
    return {
        "type": "question.asked",
        "properties": {
            "id": request_id,
            "sessionID": session_id,
            "questions": [{"question": question} for question in questions],
        },
    }


def event_session_idle(session_id: str) -> RawOpencodeEvent:
    return {"type": "session.idle", "properties": {"sessionID": session_id}}


def event_session_error(session_id: str, message: str) -> RawOpencodeEvent:
    return {
        "type": "session.error",
        "properties": {
            "sessionID": session_id,
            "error": {"name": "APIError", "data": {"message": message}},
        },
    }


def tools_protocol(tools: FakeAgentTools) -> AgentToolsProtocol:
    return cast(AgentToolsProtocol, tools)


class MentionEnforcingTools(FakeAgentTools):
    """FakeAgentTools that enforces the real send_message contract: a message
    needs at least one mention. FakeAgentTools silently accepts mention-less
    sends (which once hid a live crash), so a regression test for the
    sender-less turn must use a boundary that rejects them like production."""

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        if not mentions:
            raise BandToolError("At least one mention is required")
        return await super().send_message(content, mentions)


class RaisingSendTools(FakeAgentTools):
    """FakeAgentTools whose send_message always fails, to exercise the
    best-effort ``_notify_room`` path: a room post that raises must be
    swallowed so the turn still unblocks."""

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        raise BandToolError("send failed")


class TaskEventFailingTools(FakeAgentTools):
    """FakeAgentTools whose ``task`` events fail, to prove a transient
    task-event post failure does not abort the turn before the model runs.
    Other event types (e.g. ``error``) still succeed."""

    async def send_event(
        self,
        content: str,
        message_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if message_type == "task":
            raise BandToolError("task event post failed")
        return await super().send_event(content, message_type, metadata)


class FakeOpencodeClient:
    def __init__(
        self,
        *,
        prompt_event_sequences: list[list[RawOpencodeEvent]] | None = None,
        reply_permission_events: dict[str, list[RawOpencodeEvent]] | None = None,
        reply_question_events: dict[str, list[RawOpencodeEvent]] | None = None,
        reject_question_events: dict[str, list[RawOpencodeEvent]] | None = None,
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
        self.disconnected_mcp_servers: list[str] = []
        self.closed = False
        self._session_counter = 0
        self._queue: asyncio.Queue[RawOpencodeEvent | None] = asyncio.Queue()
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

    async def disconnect_mcp_server(self, name: str) -> None:
        self.disconnected_mcp_servers.append(name)

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


async def _run_single_turn(
    adapter: OpencodeAdapter,
    tools: FakeAgentTools,
    *,
    content: str = "hello",
) -> None:
    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(content=content),
        tools_protocol(tools),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )
