"""Shared doubles and helpers for the Codex adapter tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest_asyncio
from pydantic import BaseModel

from band.adapters.codex import (
    ApprovalDecision,
    CodexAdapter,
    CodexAdapterConfig,
    CodexCommand,
    PendingApproval,
)
from band.core.retention import Residency, RetainedRoom
from band.core.types import (
    AgentInput,
    HistoryProvider,
    PlatformMessage,
)
from band.integrations.codex import (
    DYNAMIC_TOOL_CALL_METHOD,
    TRANSPORT_CLOSED,
    RpcEvent,
)
from band.integrations.codex.types import (
    CodexApprovalMethod,
    CodexItemType,
    CodexRequestMethod,
    CodexSessionState,
)
from band.runtime.decisions import DecisionRegistry
from band.runtime.tools import BandTool
from band.testing import FakeAgentTools
from tests.adapters.codexturns import (
    MAX_LOOP_POLLS,
    FakeCodexClient,
    await_released_turn,
    event_notification,
    event_request,
    make_codex_adapter,
    turn_completed,
)
from tests.adapters.dispatch import deliver_event, deliver_message
from tests.paths import host_absolute_path

ROOM_ID = "room-1"


def make_platform_message(
    room_id: str = ROOM_ID, content: str = "hello"
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


def agent_input(
    content: str,
    tools: FakeAgentTools,
    *,
    raw_history: list[dict[str, Any]] | None = None,
    room_id: str = ROOM_ID,
    is_session_bootstrap: bool = True,
) -> AgentInput:
    return AgentInput(
        msg=make_platform_message(room_id=room_id, content=content),
        tools=tools,
        history=HistoryProvider(raw=raw_history or []),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=is_session_bootstrap,
        room_id=room_id,
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
                    "name": "band_send_event",
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


# model/list as codex-cli reports it: per-model efforts, including ones newer
# than the SDK ever knew about.
LIVE_EFFORTS_MODEL_LIST: dict[str, Any] = {
    "data": [
        {
            "id": "gpt-6-sol",
            "supportedReasoningEfforts": [
                {"reasoningEffort": "low"},
                {"reasoningEffort": "max"},
                {"reasoningEffort": "ultra"},
            ],
        },
        {"id": "other", "supportedReasoningEfforts": [{"reasoningEffort": "minimal"}]},
    ]
}


def patch_codex_clients_by_room(
    adapter: CodexAdapter, clients: dict[str, FakeCodexClient]
) -> None:
    """Give each room its own fake app-server, as the real adapter does."""

    def _build(_workspace: str) -> FakeCodexClient:
        room_id = adapter._active_room.get()
        assert room_id is not None
        return clients[room_id]

    adapter._build_client = _build  # type: ignore[method-assign]


def wire_codex_room(
    adapter: CodexAdapter,
    client: FakeCodexClient,
    room_id: str = ROOM_ID,
) -> None:
    adapter._room_client(room_id).client = client
    adapter._active_room.set(room_id)


async def start_room_process(adapter: CodexAdapter, room_id: str = ROOM_ID) -> None:
    """Bring up ``room_id``'s Codex process through a local command's lease."""
    await deliver_message(
        adapter,
        make_platform_message(room_id=room_id, content=f"/{CodexCommand.MODEL}"),
        ToolSchemaFakeTools(),
        CodexSessionState(),
        None,
        None,
        is_session_bootstrap=False,
        room_id=room_id,
    )


@dataclass(frozen=True)
class CodexTurn:
    """What one driven turn left behind, as the projections tests assert on."""

    adapter: CodexAdapter
    client: FakeCodexClient
    tools: FakeAgentTools

    @property
    def tool_response(self) -> tuple[int | str, dict[str, Any]]:
        """``(request_id, payload)`` of the first tool-call response sent back."""
        return self.client.responses[0]

    @property
    def content_items(self) -> list[dict[str, Any]]:
        """Content items the adapter returned for the first tool call."""
        return self.tool_response[1]["contentItems"]


async def run_codex_turn(
    *,
    events: list[RpcEvent],
    tools: FakeAgentTools | None = None,
    config: CodexAdapterConfig | None = None,
    **adapter_kwargs: Any,
) -> CodexTurn:
    """Drive one full Codex turn against ``events`` and return what it produced.

    Wraps the scaffolding a turn test otherwise repeats -- fake transport,
    adapter wired to it, ``on_started``, one bootstrap ``on_message`` -- so a
    test states only the events it scripts and the outcome it asserts.
    """
    client = FakeCodexClient(events=events)
    adapter = make_codex_adapter(client, config=config, **adapter_kwargs)
    room_tools = tools if tools is not None else ToolSchemaFakeTools()

    await adapter.on_started("Codex Agent", "A coding agent")
    await send_bootstrap(adapter, tools=room_tools)
    return CodexTurn(adapter=adapter, client=client, tools=room_tools)


async def send_bootstrap(
    adapter: CodexAdapter,
    *,
    room_id: str = ROOM_ID,
    tools: FakeAgentTools | None = None,
    history: CodexSessionState | None = None,
) -> None:
    """Deliver a room's session-bootstrap message to ``adapter``."""
    await deliver_message(
        adapter,
        make_platform_message(room_id=room_id),
        tools if tools is not None else ToolSchemaFakeTools(),
        history or CodexSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id=room_id,
    )


class CodexRoom:
    """A Codex room fed messages one at a time, as Band delivers them: each
    ``send`` gets its own tools and returns once the adapter hands the room
    back -- its turn done, or parked on a human decision."""

    def __init__(self, adapter: CodexAdapter, client: FakeCodexClient) -> None:
        self.adapter = adapter
        self.client = client
        self.deliveries: list[FakeAgentTools] = []

    @property
    def chat(self) -> list[str]:
        """Room messages, grouped by the delivery whose tools posted them."""
        return [
            message["content"]
            for tools in self.deliveries
            for message in tools.messages_sent
        ]

    @property
    def events_sent(self) -> list[dict[str, Any]]:
        return [event for tools in self.deliveries for event in tools.events_sent]

    @property
    def turn(self) -> asyncio.Task[None]:
        return self.adapter._turn_tasks[ROOM_ID].task

    async def send(self, content: str) -> None:
        self.deliveries.append(tools := ToolSchemaFakeTools())
        await deliver_event(
            self.adapter,
            agent_input(content, tools, is_session_bootstrap=len(self.deliveries) == 1),
        )

    async def settled(self) -> None:
        """Wait for a turn a human decision released to finish."""
        await await_released_turn(self.adapter, ROOM_ID)


def delegated_to(thread_id: str) -> RpcEvent:
    """The turn's collab item naming the sub-agent thread it spawned."""
    return event_notification(
        "item/completed",
        {
            "item": {
                "type": CodexItemType.COLLAB_AGENT_TOOL_CALL,
                "id": f"spawn-{thread_id}",
                "tool": "spawnAgent",
                "status": "completed",
                "receiverThreadIds": [thread_id],
            }
        },
    )


def started_sub_agent(thread_id: str) -> RpcEvent:
    """The item ``multi_agent_v2`` sends when the turn spawns a sub-agent."""
    return event_notification(
        "item/completed",
        {
            "item": {
                "type": CodexItemType.SUB_AGENT_ACTIVITY,
                "id": f"activity-{thread_id}",
                "kind": "started",
                "agentThreadId": thread_id,
            }
        },
    )


def asked_by_sub_agent(request_id: int, thread_id: str) -> RpcEvent:
    """An approval a sub-agent asks under its own thread and turn."""
    return event_request(
        request_id,
        CodexApprovalMethod.COMMAND_EXECUTION,
        {"threadId": thread_id, "turnId": f"{thread_id}-turn", "command": "make"},
    )


def parked_on_approval(*then: RpcEvent) -> tuple[RpcEvent, ...]:
    """A turn that asks the room to approve one command, then plays ``then``."""
    return (
        event_request(10, CodexApprovalMethod.COMMAND_EXECUTION, {"command": "a"}),
        *then,
    )


class CodexApprovalRoom:
    """One room's manual approvals on a started Codex adapter, observed
    through the room's chat: each ask runs as its own turn, and replies go
    through the room's approval commands."""

    def __init__(self, adapter: CodexAdapter) -> None:
        self.adapter = adapter
        self.tools = FakeAgentTools()
        self.asks: list[asyncio.Task[ApprovalDecision]] = []

    @property
    def pending(self) -> DecisionRegistry[PendingApproval]:
        return self.adapter._pending_approvals[ROOM_ID]

    def ask(
        self, approval_id: str, *, request_id: int
    ) -> asyncio.Task[ApprovalDecision]:
        params = {"approvalId": approval_id, "command": "npm test"}
        self.asks.append(
            ask := asyncio.create_task(
                self.adapter._resolve_manual_approval(
                    tools=self.tools,
                    msg=make_platform_message(room_id=ROOM_ID),
                    room_id=ROOM_ID,
                    event=event_request(
                        request_id, CodexApprovalMethod.COMMAND_EXECUTION, params
                    ),
                    summary="npm test",
                    params=params,
                )
            )
        )
        return ask

    @asynccontextmanager
    async def prompted(self, *approval_ids: str) -> AsyncIterator[None]:
        """Asks made inside the block have each been registered and their
        room prompt sent by the time it exits."""
        prompts = [self.tools.hold_message(f"`{id_}`") for id_ in approval_ids]
        yield
        for prompt in prompts:
            async with prompt:
                pass

    async def reply(self, command: CodexCommand, approval_id: str = "") -> str:
        """Send an approval command to the room; the room's answer."""
        await self.adapter._handle_approval_command(
            tools=self.tools,
            msg=make_platform_message(room_id=ROOM_ID),
            room_id=ROOM_ID,
            command=command,
            args=approval_id,
        )
        return self.tools.messages_sent[-1]["content"]


SKILL_ROOT = host_absolute_path("opt", "band", "skills")


REGISTER_SKILL_ROOT = (
    CodexRequestMethod.SKILLS_EXTRA_ROOTS_SET,
    {"extraRoots": [SKILL_ROOT]},
)


class StayQuietInput(BaseModel):
    """Say nothing this turn."""


async def answer_to(client: FakeCodexClient, request_id: int | str) -> dict[str, Any]:
    """The response the adapter sent ``request_id``, once it has."""
    for _ in range(MAX_LOOP_POLLS):
        for answered, result in client.responses:
            if answered == request_id:
                return result
        await asyncio.sleep(0)
    raise AssertionError(f"Request {request_id} was never answered")


class SlowTurnStartClient(FakeCodexClient):
    """An app-server whose turn/start answer trails the turn's first events."""

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry_on_overload: bool = True,
    ) -> dict[str, Any]:
        result = await super().request(
            method, params, retry_on_overload=retry_on_overload
        )
        if method == CodexRequestMethod.TURN_START:
            await self.read_by_adapter()
        return result


class CompletesWhileRefusingClient(SlowTurnStartClient):
    """An app-server that completes its turn while the adapter answers a
    request no turn owns."""

    async def respond(self, request_id: int | str, result: dict[str, Any]) -> None:
        await super().respond(request_id, result)
        self.emit(turn_completed())
        await self.read_by_adapter()


class StopsReadingAfterTurnClient(SlowTurnStartClient):
    """An app-server that asks one more thing after its turn completes, then
    never reads the answer."""

    def _release_turn_events(self, turn_id: str) -> None:
        super()._release_turn_events(turn_id)
        self.emit(
            event_request(
                9,
                DYNAMIC_TOOL_CALL_METHOD,
                {"turnId": turn_id, "tool": BandTool.SEND_MESSAGE},
            )
        )

    async def respond(self, request_id: int | str, result: dict[str, Any]) -> None:
        await asyncio.Event().wait()


# The notice a client queues once its Codex process has exited.
PROCESS_EXITED = event_notification(TRANSPORT_CLOSED, {"reason": "exited"})


class ExitsWhileAskedClient(FakeCodexClient):
    """An app-server that exits while the room decides what it asked: an
    answer can no longer be written to it, as with a real exited child."""

    async def respond(self, request_id: int | str, result: dict[str, Any]) -> None:
        raise ConnectionResetError("Connection lost")


class CountingCloseClient(FakeCodexClient):
    """Counts process starts and physical closes, optionally holding each
    close open or refusing it."""

    def __init__(self, *, events: list[RpcEvent], hold: bool = False) -> None:
        super().__init__(events=events)
        self.starts = 0
        self.closes = 0
        self.refuse_close = False
        self.closing = asyncio.Event()
        self.finish_close = asyncio.Event()
        self.close_finished = asyncio.Event()
        if not hold:
            self.finish_close.set()

    async def initialize(self, **kwargs: Any) -> dict[str, Any]:
        self.starts += 1
        return await super().initialize(**kwargs)

    async def close(self) -> None:
        self.closes += 1
        self.closing.set()
        await self.finish_close.wait()
        if self.refuse_close:
            raise RuntimeError("Codex process close failed")
        await super().close()
        self.close_finished.set()


class HeldStartClient(CountingCloseClient):
    """An app-server whose initialize handshake waits until released."""

    def __init__(self, *, events: list[RpcEvent]) -> None:
        super().__init__(events=events)
        self.finish_start = asyncio.Event()

    async def initialize(self, **kwargs: Any) -> dict[str, Any]:
        await self.finish_start.wait()
        return await super().initialize(**kwargs)


class HeldTurnClient(CountingCloseClient):
    """An app-server whose turn/start waits until released."""

    def __init__(self, *, events: list[RpcEvent], hold: bool = False) -> None:
        super().__init__(events=events, hold=hold)
        self.turn_started = asyncio.Event()
        self.finish_turn = asyncio.Event()

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry_on_overload: bool = True,
    ) -> dict[str, Any]:
        if method == CodexRequestMethod.TURN_START:
            self.turn_started.set()
            await self.finish_turn.wait()
        return await super().request(
            method, params, retry_on_overload=retry_on_overload
        )


# Adapters run their turns on the loop that started them, so setup, the test
# and teardown share the test's own loop.
@pytest_asyncio.fixture(loop_scope="function")
async def codex_room() -> AsyncIterator[Callable[..., Awaitable[CodexRoom]]]:
    """Open a room on a started manual-approval Codex adapter whose server
    plays ``events``; every room is cleaned up at the end."""
    rooms: list[CodexRoom] = []

    async def open_room(
        *events: RpcEvent, client: FakeCodexClient | None = None, **config: Any
    ) -> CodexRoom:
        client = client or FakeCodexClient(events=list(events))
        adapter = make_codex_adapter(
            client, config=CodexAdapterConfig(**{"approval_mode": "manual", **config})
        )
        await adapter.on_started("Agent", "A coding agent")
        rooms.append(room := CodexRoom(adapter, client))
        return room

    yield open_room
    for room in rooms:
        await room.adapter.on_cleanup(ROOM_ID)


async def disposed(residency: RetainedRoom) -> None:
    """Wait until an owned disposal that outlived its caller has finished."""
    for _ in range(MAX_LOOP_POLLS):
        if residency.snapshot.state == Residency.CLOSED:
            return
        await asyncio.sleep(0)
    raise AssertionError("The room's disposal never finished")
