"""Shared fakes and helpers for CopilotSDKAdapter tests.

The Copilot SDK client/session are faked at the adapter's ``client_factory``
seam (mirroring FakeCodexClient); the adapter, converter, and tool bridging
under test are real, exercised through the SDK's standard FakeAgentTools
test double.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from band.adapters.copilot_sdk import (
    _COPILOT_SDK_AVAILABLE,
    CopilotSDKAdapter,
    CopilotSDKAdapterConfig,
)
from band.converters.copilot_sdk import CopilotSDKSessionState
from band.core.types import PlatformMessage
from band.testing import FakeAgentTools

requires_copilot_sdk = pytest.mark.skipif(
    not _COPILOT_SDK_AVAILABLE,
    reason="github-copilot-sdk not installed (pip install band-sdk[copilot_sdk])",
)

if _COPILOT_SDK_AVAILABLE:
    from copilot.generated.session_events import SessionErrorData


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
                    "name": "band_send_message",
                    "description": "Send a message",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "mentions": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["content", "mentions"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "band_get_participants",
                    "description": "List room participants",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]


class FakeCopilotSession:
    """Fake session: records prompts, replays scripted events to subscribers."""

    def __init__(
        self,
        session_id: str | None,
        kwargs: dict[str, Any],
        *,
        resumed: bool = False,
        reply_content: str | None = "Hello from Copilot",
        turn_events: list[Any] | None = None,
    ):
        self.session_id = session_id
        self.kwargs = kwargs
        self.resumed = resumed
        self.reply_content = reply_content
        self.turn_events = turn_events or []
        self.send_error: Exception | None = None
        self.prompts: list[str] = []
        self.handlers: list[Any] = []
        self.aborted = False
        self.disconnected = False

    def on(self, handler: Any) -> Any:
        self.handlers.append(handler)
        return lambda: self.handlers.remove(handler)

    def find_tool(self, name: str) -> Any:
        return next(t for t in self.kwargs.get("tools") or [] if t.name == name)

    async def send_and_wait(
        self, prompt: str, *, timeout: float = 60.0, **_: Any
    ) -> Any:
        self.prompts.append(prompt)
        if self.send_error:
            raise self.send_error
        for data in self.turn_events:
            event = SimpleNamespace(data=data)
            for handler in list(self.handlers):
                if callable(data):
                    continue
                handler(event)
        # A callable in turn_events simulates mid-turn tool execution.
        for data in self.turn_events:
            if callable(data):
                await data(self)
        # Mirror the real SDK: any session error event makes send_and_wait
        # raise (there is no non-fatal error path).
        for data in self.turn_events:
            if isinstance(data, SessionErrorData):
                raise Exception(f"Session error: {data.message}")
        if self.reply_content is None:
            return None
        return SimpleNamespace(data=SimpleNamespace(content=self.reply_content))

    async def abort(self) -> None:
        self.aborted = True

    async def disconnect(self) -> None:
        self.disconnected = True


class FakeCopilotClient:
    """Fake client: records create/resume calls, mints FakeCopilotSessions."""

    def __init__(
        self,
        *,
        resume_error: Exception | None = None,
        reply_content: str | None = "Hello from Copilot",
        turn_events: list[Any] | None = None,
    ):
        self.resume_error = resume_error
        self.reply_content = reply_content
        self.turn_events = turn_events or []
        self.started = False
        self.stopped = False
        self.sessions: list[FakeCopilotSession] = []
        self.resume_calls: list[str] = []

    async def start(self) -> None:
        self.started = True
        self.start_calls = getattr(self, "start_calls", 0) + 1

    async def stop(self) -> None:
        self.stopped = True

    async def create_session(
        self, *, session_id: str | None = None, **kwargs: Any
    ) -> Any:
        session = FakeCopilotSession(
            session_id,
            kwargs,
            reply_content=self.reply_content,
            turn_events=self.turn_events,
        )
        self.sessions.append(session)
        return session

    async def resume_session(self, session_id: str, **kwargs: Any) -> Any:
        self.resume_calls.append(session_id)
        if self.resume_error:
            raise self.resume_error
        session = FakeCopilotSession(
            session_id,
            kwargs,
            resumed=True,
            reply_content=self.reply_content,
            turn_events=self.turn_events,
        )
        self.sessions.append(session)
        return session


async def make_started_adapter(
    client: FakeCopilotClient,
    config: CopilotSDKAdapterConfig | None = None,
    **adapter_kwargs: Any,
) -> CopilotSDKAdapter:
    adapter = CopilotSDKAdapter(config, client_factory=lambda: client, **adapter_kwargs)
    await adapter.on_started("Copilot Agent", "A helpful test agent")
    return adapter


async def run_message(
    adapter: CopilotSDKAdapter,
    tools: FakeAgentTools,
    *,
    room_id: str = "room-1",
    content: str = "hello",
    history: CopilotSDKSessionState | None = None,
    is_session_bootstrap: bool = True,
) -> PlatformMessage:
    msg = make_platform_message(room_id=room_id, content=content)
    await adapter.on_message(
        msg=msg,
        tools=tools,
        history=history or CopilotSDKSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=is_session_bootstrap,
        room_id=room_id,
    )
    return msg
