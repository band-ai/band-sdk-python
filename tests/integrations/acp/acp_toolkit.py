"""Ergonomic test toolkit for the ACP client adapter.

The goal (mirroring the e2e baseline toolkit): tests read like intent, not
plumbing. Two primitives:

* :class:`FakeACPAgent` — a scripted, in-process ACP *agent*. Script it fluently
  (``.will_say(...)``, ``.will_call_tool(...)``, ``.will_ask_permission()``) or take
  full control with the ``@agent.on_prompt`` decorator. It speaks real ACP over the
  wire; only the "LLM" is canned.
* :func:`acp_adapter` — an async context manager that starts a real
  ``ACPClientAdapter`` wired to the agent over an **in-process socketpair** (genuine
  ACP JSON-RPC, no subprocess, no LLM) and yields an :class:`AcpSession` driver whose
  ``send()`` returns a readable :class:`Reply`.

Example::

    agent = FakeACPAgent().will_say("The weather is sunny.")
    async with acp_adapter(agent) as session:
        reply = await session.send("weather?", room="room-1")
    assert reply.texts == ["The weather is sunny."]
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from acp import connect_to_agent
from acp.agent.connection import AgentSideConnection
from acp.helpers import (
    plan_entry,
    start_tool_call,
    update_agent_message_text,
    update_agent_thought_text,
    update_plan,
    update_tool_call,
)
from acp.schema import (
    AgentCapabilities,
    InitializeResponse,
    McpCapabilities,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    ToolCallUpdate,
)

from band.core.types import PlatformMessage
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.integrations.acp.client_types import ACPClientSessionState
from band.testing import FakeAgentTools

PromptHandler = Callable[["FakeACPAgent", str], Awaitable[None]]
_SESSION_EVENT_MARKER = "acp_client_session_id"  # the adapter's trailing task event


# ---------------------------------------------------------------------------
# Low-level transport-seam double (spy over ACPRuntime's spawn_process contract).
# Use FakeSpawn to unit-test the runtime/adapter's own call sequence (initialize,
# transport_kwargs, capability selection) with a scripted connection; use the
# higher-level FakeACPAgent + acp_adapter (below) to test behavior over a real wire.
# ---------------------------------------------------------------------------


def make_acp_connection(*, http: bool = True, sse: bool = False) -> AsyncMock:
    """A scripted ACP connection whose init advertises the given MCP capabilities."""
    conn = AsyncMock()
    conn.initialize = AsyncMock(
        return_value=MagicMock(
            agent_capabilities=MagicMock(mcp_capabilities=MagicMock(http=http, sse=sse))
        )
    )
    return conn


@dataclass
class FakeSpawn:
    """A fake ``spawn_process`` seam: records calls (spy) and yields a scripted conn.

    Drop-in for the injectable ``spawn_process`` on ``ACPClientAdapter``/``ACPRuntime``
    so tests exercise the real transport seam by dependency injection instead of
    patching module globals. The instance *is* the callable and returns an async
    context manager, matching the runtime's contract:
    ``spawn(client, *command, env=..., transport_kwargs=...) -> CM yielding (conn, proc)``.
    """

    conn: Any = field(default_factory=make_acp_connection)
    proc: Any = field(default_factory=MagicMock)
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)

    @asynccontextmanager
    async def __call__(
        self, client: Any, *args: Any, **kwargs: Any
    ) -> AsyncIterator[tuple[Any, Any]]:
        self.calls.append((args, kwargs))
        yield self.conn, self.proc

    @property
    def last_call(self) -> tuple[tuple[Any, ...], dict[str, Any]]:
        return self.calls[-1]

    @property
    def last_kwargs(self) -> dict[str, Any]:
        return self.calls[-1][1]


class FakeACPAgent:
    """A scripted, in-process ACP agent (the peer an ACP client drives).

    Implements the ``acp.Agent`` protocol methods the client flow calls and pushes
    canned ``session_update`` chunks back over the real connection. Script it with
    the fluent ``will_*`` builders (emitted, in order, on every prompt) or override
    entirely with the ``@agent.on_prompt`` decorator.
    """

    def __init__(self, *, http: bool = True, sse: bool = False) -> None:
        self._http = http
        self._sse = sse
        self._conn: AgentSideConnection | None = None
        self._script: list[PromptHandler] = []
        self._custom: PromptHandler | None = None
        # Observability for assertions:
        self.sessions: list[dict[str, Any]] = []
        self.prompts: list[dict[str, Any]] = []
        self.permission_responses: list[Any] = []
        self.approved: bool | None = None

    # -- scripting ---------------------------------------------------------------

    def on_prompt(self, handler: PromptHandler) -> PromptHandler:
        """Decorator: register a full-control async handler ``(agent, session_id)``.

        Wins over any ``will_*`` script. Returns the handler unchanged so it reads as
        a normal decorator.
        """
        self._custom = handler
        return handler

    def will_say(self, text: str) -> FakeACPAgent:
        self._script.append(lambda a, sid: a.say(sid, text))
        return self

    def will_stream(self, *parts: str) -> FakeACPAgent:
        """Emit several agent_message_chunk deltas in one turn (streaming reply)."""

        async def _action(a: FakeACPAgent, sid: str) -> None:
            for part in parts:
                await a.emit(sid, update_agent_message_text(part))

        self._script.append(_action)
        return self

    def will_think(self, text: str) -> FakeACPAgent:
        self._script.append(lambda a, sid: a.emit(sid, update_agent_thought_text(text)))
        return self

    def will_call_tool(
        self,
        tool_call_id: str,
        title: str,
        *,
        raw_input: Any = None,
        result: Any = None,
        status: str = "completed",
    ) -> FakeACPAgent:
        async def _action(a: FakeACPAgent, sid: str) -> None:
            await a.emit(sid, start_tool_call(tool_call_id, title, raw_input=raw_input))
            if result is not None:
                await a.emit(
                    sid,
                    update_tool_call(tool_call_id, raw_output=result, status=status),
                )

        self._script.append(_action)
        return self

    def will_plan(self, *steps: str) -> FakeACPAgent:
        self._script.append(
            lambda a, sid: a.emit(sid, update_plan([plan_entry(s) for s in steps]))
        )
        return self

    def will_ask_permission(
        self, *, tool_call_id: str = "tc-1", allow_option_id: str = "allow-1"
    ) -> FakeACPAgent:
        async def _action(a: FakeACPAgent, sid: str) -> None:
            resp = await a.ask_permission(
                sid,
                ToolCallUpdate(tool_call_id=tool_call_id),
                [
                    PermissionOption(
                        kind="allow_once", name="Allow", optionId=allow_option_id
                    ),
                    PermissionOption(
                        kind="reject_once", name="Reject", optionId="reject-1"
                    ),
                ],
            )
            a.approved = allow_option_id in str(resp)

        self._script.append(_action)
        return self

    # -- agent-side emit helpers -------------------------------------------------

    async def say(self, session_id: str, text: str) -> None:
        await self.emit(session_id, update_agent_message_text(text))

    async def emit(self, session_id: str, update: Any) -> None:
        assert self._conn is not None, "agent not connected yet"
        await self._conn.session_update(session_id, update)

    async def ask_permission(
        self, session_id: str, tool_call: Any, options: list[Any]
    ) -> Any:
        assert self._conn is not None, "agent not connected yet"
        resp = await self._conn.request_permission(
            options=options, session_id=session_id, tool_call=tool_call
        )
        self.permission_responses.append(resp)
        return resp

    # -- acp.Agent protocol ------------------------------------------------------

    def on_connect(self, conn: AgentSideConnection) -> None:
        self._conn = conn

    async def initialize(
        self, protocol_version: int, client_capabilities: Any = None, **kwargs: Any
    ) -> InitializeResponse:
        del client_capabilities, kwargs
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(
                mcp_capabilities=McpCapabilities(http=self._http, sse=self._sse)
            ),
        )

    async def new_session(
        self, cwd: str, mcp_servers: Any = None, **kwargs: Any
    ) -> NewSessionResponse:
        del kwargs
        sid = f"fake-session-{len(self.sessions) + 1}"
        self.sessions.append(
            {"session_id": sid, "cwd": cwd, "mcp_servers": list(mcp_servers or [])}
        )
        return NewSessionResponse(session_id=sid)

    async def prompt(
        self, prompt: Any, session_id: str, message_id: str | None = None, **kwargs: Any
    ) -> PromptResponse:
        del message_id, kwargs
        self.prompts.append({"session_id": session_id, "prompt": prompt})
        if self._custom is not None:
            await self._custom(self, session_id)
        else:
            for action in self._script:
                await action(self, session_id)
        return PromptResponse(stop_reason="end_turn")


@dataclass
class Reply:
    """A readable view of what the adapter posted back for one turn."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def _events_of(self, message_type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("message_type") == message_type]

    @property
    def texts(self) -> list[str]:
        return [m["content"] for m in self.messages]

    @property
    def thoughts(self) -> list[str]:
        return [e["content"] for e in self._events_of("thought")]

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return [
            e
            for e in self._events_of("tool_call")
            if not (e.get("metadata") or {}).get("permission_request")
        ]

    @property
    def tool_results(self) -> list[dict[str, Any]]:
        return self._events_of("tool_result")

    @property
    def plans(self) -> list[str]:
        # Task events, minus the adapter's trailing "ACP client session" bookkeeping.
        return [
            e["content"]
            for e in self._events_of("task")
            if _SESSION_EVENT_MARKER not in (e.get("metadata") or {})
        ]

    @property
    def permissions(self) -> list[dict[str, Any]]:
        return [
            e
            for e in self._events_of("tool_call")
            if (e.get("metadata") or {}).get("permission_request")
        ]


class AcpSession:
    """Driver over a started ACPClientAdapter — send messages, read back effects."""

    def __init__(self, adapter: ACPClientAdapter, agent: FakeACPAgent) -> None:
        self.adapter = adapter
        self.agent = agent

    async def send(self, content: str, *, room: str = "room-1") -> Reply:
        """Deliver ``content`` to ``room`` and return what the adapter posted back."""
        tools = FakeAgentTools()
        await self.adapter.on_message(
            _message(content, room),
            tools,
            ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id=room,
        )
        return Reply(messages=tools.messages_sent, events=tools.events_sent)

    def session_id(self, room: str) -> str:
        return self.adapter._room_to_session[room]


@asynccontextmanager
async def acp_adapter(
    agent: FakeACPAgent, *, inject_band_tools: bool = False, **adapter_kwargs: Any
) -> AsyncIterator[AcpSession]:
    """A started ``ACPClientAdapter`` wired to ``agent`` over an in-process socketpair.

    Yields an :class:`AcpSession`; tears the adapter down on exit.
    """
    adapter = ACPClientAdapter(
        command="fake-agent",  # ignored — the injected transport pairs us with agent
        spawn_process=_pair_in_process(agent),
        inject_band_tools=inject_band_tools,
        **adapter_kwargs,
    )
    await adapter.on_started("Fake Agent", "in-process fake")
    try:
        yield AcpSession(adapter, agent)
    finally:
        await adapter.stop()


def _pair_in_process(agent: FakeACPAgent) -> Callable[..., Any]:
    """A ``spawn_process`` that pairs the adapter's client with ``agent`` over a
    socketpair — real ACP JSON-RPC on real asyncio streams, no subprocess."""

    @asynccontextmanager
    async def _spawn(
        client: Any, *args: Any, env: Any = None, transport_kwargs: Any = None
    ) -> AsyncIterator[tuple[Any, Any]]:
        del args, env, transport_kwargs
        client_sock, agent_sock = socket.socketpair()
        reader_c, writer_c = await asyncio.open_connection(sock=client_sock)
        reader_a, writer_a = await asyncio.open_connection(sock=agent_sock)
        # listening=True starts the agent's receive loop and fires agent.on_connect.
        agent_conn = AgentSideConnection(agent, writer_a, reader_a)
        conn = connect_to_agent(client, writer_c, reader_c)
        try:
            yield conn, agent_conn
        finally:
            for closable in (conn, agent_conn):
                with contextlib.suppress(Exception):
                    await closable.close()
            for writer in (writer_c, writer_a):
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

    return _spawn


def _message(content: str, room_id: str) -> PlatformMessage:
    return PlatformMessage(
        id=str(uuid4()),
        room_id=room_id,
        content=content,
        sender_id="peer-1",
        sender_type="Agent",
        sender_name="Peer",
        message_type="text",
        metadata={},
        created_at=datetime.now(),
    )
