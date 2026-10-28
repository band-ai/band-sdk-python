"""Shared fakes and helpers for CopilotSDKAdapter tests.

The Copilot SDK client/session are faked at the adapter's ``client_factory``
seam (mirroring FakeCodexClient); the adapter, converter, and tool bridging
under test are real, exercised through the SDK's standard FakeAgentTools
test double.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Sequence
from datetime import UTC, datetime
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
from band.core.types import USAGE_METADATA_KEY, MessageType, PlatformMessage
from band.testing import FakeAgentTools
from tests.adapters.audiences import ROOM_MEMBERS
from tests.framework_conformance.turnprobes import turn_input

requires_copilot_sdk = pytest.mark.skipif(
    not _COPILOT_SDK_AVAILABLE,
    reason="github-copilot-sdk not installed (pip install band-sdk[copilot_sdk])",
)

if _COPILOT_SDK_AVAILABLE:
    from copilot.generated.session_events import (
        AssistantUsageData,
        SessionErrorData,
        SessionIdleData,
    )

# An earlier room message, and the line it becomes in a replayed transcript.
EARLIER_MESSAGE = "my name is Alice"
EARLIER_LINE = f"[Alice]: {EARLIER_MESSAGE}"


class ObservedCopilotAdapter(CopilotSDKAdapter):
    """Expose turn entry while preserving the real room-lock acquisition."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.turn_entries: asyncio.Queue[tuple[str, ...]] = asyncio.Queue()

    async def on_turn(
        self, messages: Sequence[PlatformMessage], *args: Any, **kwargs: Any
    ) -> None:
        # No suspension separates this signal from the real turn's room lock.
        self.turn_entries.put_nowait(tuple(message.id for message in messages))
        await super().on_turn(messages, *args, **kwargs)


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
        created_at=datetime.now(UTC),
    )


def room_message(message_id: str, content: str) -> dict[str, Any]:
    """Alice's text message as the room context stores it."""
    return {
        "id": message_id,
        "message_type": MessageType.TEXT,
        "sender_id": "user-1",
        "sender_type": "User",
        "sender_name": "Alice",
        "content": content,
    }


class ToolSchemaFakeTools(FakeAgentTools):
    def __init__(self) -> None:
        super().__init__(participants=ROOM_MEMBERS)

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


class HeldRestTools(ToolSchemaFakeTools):
    """Room-context fetches or usage posts park at the REST boundary while held."""

    def __init__(self) -> None:
        super().__init__()
        self.hold_context = False
        self.hold_usage = False
        self.parked = asyncio.Event()

    async def fetch_room_context(self, **kwargs: Any) -> dict[str, Any]:
        if self.hold_context:
            await self._park()
        return await super().fetch_room_context(**kwargs)

    async def send_event(
        self,
        content: str,
        message_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        if self.hold_usage and metadata and USAGE_METADATA_KEY in metadata:
            await self._park()
        return await super().send_event(content, message_type, metadata)

    async def _park(self) -> None:
        self.parked.set()
        await asyncio.Event().wait()  # until the turn is interrupted


def room_with_earlier_message() -> HeldRestTools:
    """A room whose transcript holds Alice's ``EARLIER_MESSAGE``."""
    tools = HeldRestTools()
    tools.set_room_context([room_message("earlier", EARLIER_MESSAGE)])
    return tools


def usage_data(*, input_tokens: int, output_tokens: int) -> Any:
    """One model call's ``assistant.usage`` event data."""
    return AssistantUsageData(
        model="m", input_tokens=input_tokens, output_tokens=output_tokens
    )


def is_fresh_session_id(session_id: str, *, prefix: str, room_id: str) -> bool:
    """Whether ``session_id`` is a fresh session id minted for ``room_id``."""
    pattern = f"{re.escape(prefix)}{re.escape(room_id)}-[0-9a-f]{{32}}"
    return re.fullmatch(pattern, session_id) is not None


class PromptHold:
    """A turn event that parks the prompt after ``after`` finished ones until
    the turn is interrupted; every other prompt finishes."""

    def __init__(self, *, after: int = 0) -> None:
        self.reached = asyncio.Event()
        self.finishing = after

    async def __call__(self, _session: FakeCopilotSession) -> None:
        if self.reached.is_set():
            return
        if self.finishing:
            self.finishing -= 1
            return
        self.reached.set()
        await asyncio.Event().wait()  # until the turn is interrupted


async def interrupt_when(turn: asyncio.Task[Any], reached: asyncio.Event) -> None:
    """Interrupt ``turn`` once it reaches ``reached``, as the runtime's stop does.

    Fails when the turn ends before reaching it.
    """
    reaching = asyncio.create_task(reached.wait())
    await asyncio.wait({turn, reaching}, return_when=asyncio.FIRST_COMPLETED)
    if turn.done():
        reaching.cancel()
        pytest.fail(f"the turn ended before it was interrupted: {turn!r}")
    turn.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await turn


class FakeCopilotSession:
    """Fake session: records prompts, replays scripted events to subscribers."""

    def __init__(
        self,
        session_id: str,
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
        # Every prompt, abort and disconnect, in the order the session saw them.
        self.journal: list[str] = []
        # Set once an abort reaches the session; while ``abort_released`` is
        # cleared, that abort stays in flight.
        self.aborting = asyncio.Event()
        self.abort_released = asyncio.Event()
        self.abort_released.set()
        # While set, an abort is acknowledged but the idle that ends the aborted
        # turn is withheld until ``deliver_aborted_idle``.
        self.holds_aborted_idle = False

    def on(self, handler: Any) -> Any:
        self.handlers.append(handler)
        return lambda: self.handlers.remove(handler)

    def find_tool(self, name: str) -> Any:
        return next(t for t in self.kwargs.get("tools") or [] if t.name == name)

    async def send_and_wait(
        self, prompt: str, *, timeout: float = 60.0, **_: Any
    ) -> Any:
        self.prompts.append(prompt)
        self.journal.append("prompt")
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
                raise RuntimeError(f"Session error: {data.message}")
        if self.reply_content is None:
            return None
        return SimpleNamespace(data=SimpleNamespace(content=self.reply_content))

    async def abort(self) -> None:
        self.aborting.set()
        await self.abort_released.wait()
        self.aborted = True
        self.journal.append("abort")
        if not self.holds_aborted_idle:
            self.deliver_aborted_idle()

    def deliver_aborted_idle(self) -> None:
        """The CLI ends an aborted turn with an idle, as the real one does."""
        self.journal.append("idle")
        self._publish(SessionIdleData(aborted=True))

    def _publish(self, data: Any) -> None:
        event = SimpleNamespace(data=data)
        for handler in list(self.handlers):
            handler(event)

    async def disconnect(self) -> None:
        self.disconnected = True
        self.journal.append("disconnect")


class FakeCopilotClient:
    """Fake client: records create/resume calls, mints FakeCopilotSessions."""

    def __init__(
        self,
        *,
        resume_error: Exception | None = None,
        create_error: Exception | None = None,
        reply_content: str | None = "Hello from Copilot",
        turn_events: list[Any] | None = None,
        prompt_errors: Sequence[Exception] = (),
    ):
        self.resume_error = resume_error
        self.create_error = create_error
        self.reply_content = reply_content
        self.turn_events = turn_events or []
        # Each new session's prompts raise the next of these, in order.
        self.prompt_errors = list(prompt_errors)
        self.started = False
        self.stopped = False
        self.sessions: list[FakeCopilotSession] = []
        self.resume_calls: list[str] = []
        self.deleted: list[str] = []
        self.delete_error: Exception | None = None
        # While set, a delete never gets an answer from the runtime.
        self.hold_delete = False
        # While set, each new session holds its aborts in flight; ``aborting``
        # is set once an abort reaches any of them.
        self.hold_aborts = False
        self.aborting = asyncio.Event()
        # Every id create_session was asked for; while ``hold_create`` is set,
        # the runtime creates the next one but its reply never arrives.
        self.create_calls: list[str] = []
        self.hold_create = False
        self.creating = asyncio.Event()

    async def start(self) -> None:
        self.started = True
        self.start_calls = getattr(self, "start_calls", 0) + 1

    async def stop(self) -> None:
        self.stopped = True

    async def create_session(self, *, session_id: str, **kwargs: Any) -> Any:
        self.create_calls.append(session_id)
        if self.hold_create:
            self.hold_create = False
            self.creating.set()
            await asyncio.Event().wait()  # until the turn is interrupted
        if self.create_error:
            raise self.create_error
        session = FakeCopilotSession(
            session_id,
            kwargs,
            reply_content=self.reply_content,
            turn_events=self.turn_events,
        )
        return self._mint(session)

    def _mint(self, session: FakeCopilotSession) -> FakeCopilotSession:
        if self.prompt_errors:
            session.send_error = self.prompt_errors.pop(0)
        if self.hold_aborts:
            session.abort_released.clear()
            session.aborting = self.aborting
        self.sessions.append(session)
        return session

    async def delete_session(self, session_id: str) -> None:
        if self.hold_delete:
            await asyncio.Event().wait()  # until the caller gives up
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(session_id)

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
        return self._mint(session)


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
    msg: PlatformMessage | None = None,
) -> PlatformMessage:
    msg = msg or make_platform_message(room_id=room_id, content=content)
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


async def run_event(
    adapter: CopilotSDKAdapter,
    tools: FakeAgentTools,
    *,
    room_id: str = "room-1",
    content: str = "hello",
) -> None:
    """Run one turn through ``on_event``, where the runtime judges it."""
    await adapter.on_event(
        turn_input(tools, msg=make_platform_message(room_id=room_id, content=content))
    )
