"""Harness + result view: drive a real ACPClientAdapter against a fake agent."""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from acp import connect_to_agent
from acp.agent.connection import AgentSideConnection

from band.core.types import PlatformMessage
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.integrations.acp.client_types import ACPClientSessionState
from band.testing import FakeAgentTools

from .agent import FakeACPAgent

_SESSION_EVENT_MARKER = "acp_client_session_id"  # the adapter's trailing task event


@dataclass(frozen=True)
class RoomActivity:
    """One room write, retained in the order the adapter made it."""

    kind: Literal["message", "event"]
    content: str
    message_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """A compact, human-readable tag for asserting transcript shape/order.

        e.g. ``"message"``, ``"thought"``, ``"tool_call"``, ``"task"``, and
        ``"tool_call (permission)"`` for a synthetic permission event.
        """
        if self.kind == "message":
            return "message"
        suffix = " (permission)" if self.metadata.get("permission_request") else ""
        return f"{self.message_type}{suffix}"


class TranscriptTools(FakeAgentTools):
    """Fake tools that retain the complete order of room writes."""

    def __init__(self) -> None:
        super().__init__()
        self.transcript: list[RoomActivity] = []

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        message = await super().send_message(content, mentions)
        self.transcript.append(RoomActivity(kind="message", content=content))
        return message

    async def send_event(
        self,
        content: str,
        message_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = await super().send_event(content, message_type, metadata)
        self.transcript.append(
            RoomActivity(
                kind="event",
                content=content,
                message_type=message_type,
                metadata=dict(metadata or {}),
            )
        )
        return event


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


@dataclass
class Reply:
    """A readable view of what the adapter posted back for one turn."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    transcript: list[RoomActivity] = field(default_factory=list)

    @property
    def outline(self) -> list[str]:
        """The room writes as ordered, human-readable labels (see RoomActivity.label).

        Lets a test assert the shape and order of a turn — e.g.
        ``["tool_call (permission)", "message", "tool_result (permission)", "task"]``
        — without index and metadata bookkeeping.
        """
        return [activity.label for activity in self.transcript]

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

    async def send(
        self,
        content: str,
        *,
        room: str = "room-1",
        history: ACPClientSessionState | None = None,
        bootstrap: bool = False,
    ) -> Reply:
        """Deliver ``content`` to ``room`` and return what the adapter posted back.

        ``bootstrap=True`` with a ``history`` models the first message after an
        adapter (re)start, when the runtime hands over the room's converted
        platform history.
        """
        tools = TranscriptTools()
        await self.adapter.on_message(
            _message(content, room),
            tools,
            history or ACPClientSessionState(),
            None,
            None,
            is_session_bootstrap=bootstrap,
            room_id=room,
        )
        return Reply(
            messages=tools.messages_sent,
            events=tools.events_sent,
            transcript=tools.transcript,
        )

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
