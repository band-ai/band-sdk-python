"""Shared OpenCode adapter test fakes and builders."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, TypeAlias, cast
from uuid import uuid4

import httpx
import pytest

from band.adapters.opencode import OpencodeAdapter
from band.adapters.opencode.adapter import MCP_REGISTRATION_CONNECTED_STATUS, TurnState
from band.adapters.opencode.approvals.wording import (
    APPROVAL_HANDLED_TEMPLATE,
    APPROVAL_REQUESTED_TEMPLATE,
    DECLINED_QUESTION_ANSWER,
)
from band.client.rest import MessageSentResponse
from band.core.exceptions import BandToolError
from band.core.protocols import AgentToolsProtocol
from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.integrations.mcp.engine import EmbeddedResolver
from band.integrations.opencode import (
    ApprovalReply,
    OpencodePermissionRequest,
    OpencodeQuestionRequest,
)
from band.integrations.opencode.types import OpencodeSessionState
from band.runtime.tools import TOOL_DEFINITIONS
from band.testing import FakeAgentTools
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.dispatch import deliver_event, deliver_message
from tests.mcpbackends import BackendStarts

RawOpencodeEvent: TypeAlias = dict[str, Any]
# What the fake server does next: stream an SSE event, or act between events
# (e.g. the model calling a band tool over MCP).
ServerStep: TypeAlias = RawOpencodeEvent | Callable[[], Awaitable[None]]


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
        created_at=datetime.now(UTC),
    )


#: Participants as (sender id, sender name), for turns with several senders.
ALICE = ("user-1", "Alice")
HELPER = ("agent-7", "Helper")
CAROL = ("user-3", "Carol")


def message_from(sender: tuple[str, str], content: str) -> PlatformMessage:
    sender_id, sender_name = sender
    return make_platform_message(
        content=content, sender_id=sender_id, sender_name=sender_name
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
    error: str | None = None,
) -> RawOpencodeEvent:
    state: dict[str, Any] = {"status": status, "input": input_data}
    if status == "running":
        state["time"] = {"start": 1}
    if status == "completed":
        state["output"] = "" if output is None else output
        state["title"] = tool
        state["metadata"] = {}
        state["time"] = {"start": 1, "end": 2}
    if status == "error":
        state["error"] = error or "OpenCode tool failed"
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


def model_answer(message_id: str, text: str) -> list[ServerStep]:
    """The model streams ``text`` as one assistant message, then goes idle."""
    return [
        event_message_updated("sess-1", message_id),
        event_text_part("sess-1", message_id, text),
        event_session_idle("sess-1"),
    ]


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


def agent_input(content: str, tools: FakeAgentTools) -> AgentInput:
    """What the runtime hands ``on_event`` for one room message."""
    return AgentInput(
        msg=make_platform_message(content=content),
        tools=tools_protocol(tools),
        history=HistoryProvider(raw=[]),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id="room-1",
    )


class ObservedOpencodeAdapter(OpencodeAdapter):
    """Expose watcher readiness without replacing its deadline logic."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.watcher_started = asyncio.Event()
        self.background_tasks: set[asyncio.Task[Any]] = set()

    def record_background_task(self) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.background_tasks.add(task)

    async def _run_event_loop(self) -> None:
        self.record_background_task()
        await super()._run_event_loop()

    async def _await_turn(self, turn: TurnState) -> None:
        self.record_background_task()
        self.watcher_started.set()
        await super()._await_turn(turn)


def batch_input(
    messages: Sequence[PlatformMessage],
    tools: FakeAgentTools,
    *,
    is_session_bootstrap: bool = False,
) -> AgentInput:
    """What the runtime hands the adapter for a turn over ``messages``."""
    return AgentInput(
        msg=messages[0],
        messages=tuple(messages),
        tools=tools_protocol(tools),
        history=HistoryProvider(raw=[]),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=is_session_bootstrap,
        room_id=messages[0].room_id,
    )


async def deliver_turn(adapter: OpencodeAdapter, inp: AgentInput) -> None:
    """Dispatch as the runtime does: several originals through ``on_messages``,
    one through the singleton path."""
    if len(inp.messages) > 1:
        await adapter.on_messages(inp)
    else:
        await deliver_event(adapter, inp)


class RaisingSendTools(FakeAgentTools):
    """FakeAgentTools whose send_notice always fails, to exercise the
    best-effort ``_notify_room`` path: a room post that raises must be
    swallowed so the turn still unblocks."""

    async def send_notice(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        raise BandToolError("send failed")


class HeldReplyTools(FakeAgentTools):
    """FakeAgentTools that hold every ``send_message`` until ``release`` is set,
    keeping the turn's watcher in the delivery tail after the model has finished."""

    def __init__(self) -> None:
        super().__init__(participants=ROOM_MEMBERS)
        self.reply_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> MessageSentResponse | None:
        self.reply_entered.set()
        await self.release.wait()
        return await super().send_message(content, mentions)


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
        prompt_event_sequences: list[list[ServerStep]] | None = None,
        reply_permission_events: dict[str, list[ServerStep]] | None = None,
        reply_question_events: dict[str, list[ServerStep]] | None = None,
        get_session_missing: set[str] | None = None,
        prompt_exceptions: list[Exception] | None = None,
        serve_registrations: dict[str, str] | None = None,
        register_mcp_statuses: list[str] | None = None,
    ) -> None:
        # A real ``opencode serve`` keys MCP registrations globally by name, so
        # clients sharing one serve share this mapping.
        self.serve_registrations = (
            serve_registrations if serve_registrations is not None else {}
        )
        self.created_sessions: list[dict[str, Any]] = []
        self.prompt_calls: list[dict[str, Any]] = []
        self.permission_replies: list[dict[str, Any]] = []
        self.question_replies: list[dict[str, Any]] = []
        self.aborted_sessions: list[str] = []
        self.registered_mcp_servers: list[dict[str, str]] = []
        self.disconnected_mcp_servers: list[str] = []
        self.closed = False
        self._session_counter = 0
        self._queue: asyncio.Queue[ServerStep | None] = asyncio.Queue()
        self._prompt_event_sequences = list(prompt_event_sequences or [])
        self._reply_permission_events = reply_permission_events or {}
        self._reply_question_events = reply_question_events or {}
        self._get_session_missing = get_session_missing or set()
        self._prompt_exceptions = list(prompt_exceptions or [])
        # Popped one at a time per register_mcp_server call; once exhausted,
        # every further call reports connected.
        self._register_mcp_statuses = list(register_mcp_statuses or [])

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
        tools: dict[str, bool] | None = None,
    ) -> None:
        self.prompt_calls.append(
            {
                "session_id": session_id,
                "parts": parts,
                "system": system,
                "model": model,
                "agent": agent,
                "variant": variant,
                "tools": tools,
            }
        )
        if self._prompt_exceptions:
            raise self._prompt_exceptions.pop(0)
        if self._prompt_event_sequences:
            for event in self._prompt_event_sequences.pop(0):
                await self._queue.put(event)

    async def reply_permission(
        self, permission_id: str, *, reply: ApprovalReply, message: str | None = None
    ) -> None:
        recorded = {"permission_id": permission_id, "reply": reply}
        if message:
            recorded["message"] = message
        self.permission_replies.append(recorded)
        for event in self._reply_permission_events.get(permission_id, []):
            await self._queue.put(event)

    async def reply_question(
        self, request_id: str, *, answers: list[list[str]]
    ) -> None:
        self.question_replies.append({"request_id": request_id, "answers": answers})
        for event in self._reply_question_events.get(request_id, []):
            await self._queue.put(event)

    async def abort_session(self, session_id: str) -> None:
        self.aborted_sessions.append(session_id)

    async def register_mcp_server(
        self, *, name: str, url: str, headers: dict[str, str]
    ) -> dict[str, Any]:
        self.registered_mcp_servers.append(
            {"name": name, "url": url, "headers": headers}
        )
        status = (
            self._register_mcp_statuses.pop(0)
            if self._register_mcp_statuses
            else MCP_REGISTRATION_CONNECTED_STATUS
        )
        if status == MCP_REGISTRATION_CONNECTED_STATUS:
            self.serve_registrations[name] = url
        return {name: {"status": status}}

    async def disconnect_mcp_server(self, name: str) -> None:
        self.disconnected_mcp_servers.append(name)
        self.serve_registrations.pop(name, None)

    async def push_event(self, event: ServerStep) -> None:
        """Inject one SSE event (or server action), as the server would mid-turn."""
        await self._queue.put(event)

    async def iter_events(self) -> AsyncIterator[RawOpencodeEvent]:
        while True:
            step = await self._queue.get()
            if step is None:
                return
            if callable(step):
                await step()
                continue
            yield step

    async def health(self) -> None:
        """Fake server is always reachable."""

    async def close(self) -> None:
        self.closed = True
        await self._queue.put(None)


def idle_client() -> FakeOpencodeClient:
    """A client whose single prompt goes straight to idle."""
    return FakeOpencodeClient(prompt_event_sequences=[[event_session_idle("sess-1")]])


class AnyHTTPStatusError(httpx.HTTPStatusError):
    def __init__(self, status_code: int, session_id: str) -> None:
        request = httpx.Request("GET", f"http://localhost/session/{session_id}")
        response = httpx.Response(status_code=status_code, request=request)
        super().__init__("status error", request=request, response=response)


#: The first ask each scripted OpenCode turn raises in the control tests.
PERMISSION_ASK = event_permission("sess-1", "req-1")
QUESTION_ASK = event_question("sess-1", "q-1", "Which word?")
TWO_QUESTIONS_ASK = event_question("sess-1", "q-1", "First?", "Second?")


def client_asking(*asks: RawOpencodeEvent) -> FakeOpencodeClient:
    """OpenCode runs the first turn by raising ``asks``."""
    return FakeOpencodeClient(prompt_event_sequences=[list(asks)])


def room_reply(content: str, sender_id: str = "user-2") -> PlatformMessage:
    """A room message from someone other than the first turn's sender."""
    return make_platform_message(content=content, sender_id=sender_id)


async def handle_control(adapter: OpencodeAdapter, message: PlatformMessage) -> bool:
    """Offer ``message`` to the adapter's control handler on its own delivery tools."""
    return await adapter.try_handle_control(
        message, tools_protocol(FakeAgentTools(participants=ROOM_MEMBERS))
    )


class HeldPermissionReplyClient(FakeOpencodeClient):
    """Keeps the first permission reply in flight until a test releases it."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.reply_in_flight = asyncio.Event()
        self.release_reply = asyncio.Event()

    async def reply_permission(
        self, permission_id: str, *, reply: ApprovalReply, message: str | None = None
    ) -> None:
        if not self.reply_in_flight.is_set():
            self.reply_in_flight.set()
            await self.release_reply.wait()
        await super().reply_permission(permission_id, reply=reply, message=message)


class BandMCPCalls:
    """The model calling band tools over MCP, dispatched to the rooms of the
    adapter's started backend exactly as the embedded MCP server does."""

    def __init__(self, starts: BackendStarts) -> None:
        self._starts = starts

    def band_tool_call(
        self, tool: str, arguments: dict[str, Any], *, room_id: str = "room-1"
    ) -> ServerStep:
        async def call() -> None:
            assert self._starts.requested, "the adapter never started its backend"
            resolver = EmbeddedResolver(get_tools=self._starts.requested[-1].get_tools)
            await resolver.invoke(TOOL_DEFINITIONS[tool], room_id, arguments)

        return call


#: Virtual seconds an ask waits for a human in looptime tests.
ASK_DEADLINE_S = 60.0

#: Virtual ``turn_timeout_s`` compute budget in looptime turn tests.
COMPUTE_BUDGET_S = 8.0


async def time_passes(seconds: float) -> None:
    """Let ``seconds`` of virtual time pass under ``@pytest.mark.looptime``:
    every timer due meanwhile fires, in order, with no real waiting."""
    await asyncio.sleep(seconds)


async def wait_for(predicate: Callable[[], bool], timeout_s: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    pytest.fail("Timed out waiting for condition")


async def run_single_turn(
    adapter: OpencodeAdapter,
    tools: FakeAgentTools,
    *,
    content: str = "hello",
) -> None:
    await adapter.on_started("OpenCode Agent", "A coding agent")
    await deliver_message(
        adapter,
        make_platform_message(content=content),
        tools_protocol(tools),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )


async def deliver_followup(
    adapter: OpencodeAdapter, tools: FakeAgentTools, *, content: str
) -> None:
    """Deliver a later message of the room's existing ``sess-1`` session."""
    await deliver_message(
        adapter,
        make_platform_message(content=content),
        tools_protocol(tools),
        OpencodeSessionState(room_to_session={"room-1": "sess-1"}),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id="room-1",
    )


PERMISSION_ID = "req-1"
APPROVAL_REPLY = f"approve {PERMISSION_ID}"
FOLLOW_UP = "and then?"
FIRST_ANSWER = "first answer"
SECOND_ANSWER = "second answer"


def parked_turn_client(
    *,
    after_approval: list[ServerStep],
    later_prompts: list[list[ServerStep]] | None = None,
) -> FakeOpencodeClient:
    """OpenCode asks for a permission, then runs ``after_approval``; each of
    ``later_prompts`` answers one later prompt."""
    parked: list[ServerStep] = [event_permission("sess-1", PERMISSION_ID)]
    return FakeOpencodeClient(
        prompt_event_sequences=[parked, *(later_prompts or [])],
        reply_permission_events={PERMISSION_ID: after_approval},
    )


async def park_turn(adapter: OpencodeAdapter, tools: FakeAgentTools) -> None:
    """Run a turn's ``on_event`` until the permission ask releases it."""
    await adapter.on_started("OpenCode Agent", "A coding agent")
    await deliver_event(adapter, agent_input("run it", tools))


async def approve(adapter: OpencodeAdapter) -> FakeAgentTools:
    """A person approves the parked ask; returns the approval's own tools."""
    approver_tools = FakeAgentTools(participants=ROOM_MEMBERS)
    control = agent_input(APPROVAL_REPLY, approver_tools)
    assert await adapter.try_handle_control(control.msg, control.tools)
    return approver_tools


#: The notices a ``parked_turn_client`` turn posts to the room by the time
#: ``approve`` has handled its ask.
APPROVED_ASK_NOTICES = [
    APPROVAL_REQUESTED_TEMPLATE.format(
        permission="bash", patterns="rm -rf tmp", request_id=PERMISSION_ID
    ),
    APPROVAL_HANDLED_TEMPLATE.format(request_id=PERMISSION_ID, reply="once"),
]


async def room_turn(adapter: OpencodeAdapter, *, room_id: str = "room-1") -> TurnState:
    """The turn that currently owns ``room_id``."""
    turn = (await adapter._get_or_create_room_state(room_id)).turn
    assert turn is not None
    return turn


async def turn_finished(turn: TurnState) -> None:
    """Wait until the turn's watcher has delivered its result."""
    assert turn.watcher is not None
    await turn.watcher


def assert_left_untouched(tools: FakeAgentTools) -> None:
    """A refused delivery neither ran nor judged a turn of its own."""
    assert not tools.turn.complete
    assert tools.events_sent == []
    assert tools.messages_sent == []


class AskFactory:
    """OpenCode asks with unique ids and throwaway content, so a test names
    only what it is about -- usually the id a room reply quotes back."""

    def permission(
        self, id: str | None = None, *, permission: str = "bash"
    ) -> OpencodePermissionRequest:
        return OpencodePermissionRequest(
            id=id or _unique_id("perm"), permission=permission
        )

    def question(
        self, id: str | None = None, *, questions: list[str] | None = None
    ) -> OpencodeQuestionRequest:
        texts = [f"Question {_unique_id('q')}?"] if questions is None else questions
        return OpencodeQuestionRequest(
            id=id or _unique_id("q"), questions=[{"question": t} for t in texts]
        )


def _unique_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _is_decline(reply: dict[str, Any]) -> bool:
    return all(answer == [DECLINED_QUESTION_ANSWER] for answer in reply["answers"])


def declined_questions(client: FakeOpencodeClient) -> list[str]:
    """Ids of the questions the adapter rejected by answering with the decline."""
    return [r["request_id"] for r in client.question_replies if _is_decline(r)]


def answered_questions(client: FakeOpencodeClient) -> list[tuple[str, list[list[str]]]]:
    """The room's real answers, as (question id, answers), declines left out."""
    return [
        (r["request_id"], r["answers"])
        for r in client.question_replies
        if not _is_decline(r)
    ]
