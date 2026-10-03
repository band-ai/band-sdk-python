"""Real Band MCP servers and client sessions for tests that dial them."""

from __future__ import annotations

import asyncio
import inspect
import itertools
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Tool

from band.integrations.mcp import (
    BandMCPBackend,
    BandMCPBackendSettings,
    BandMCPTransport,
    SharedBandMCPBackend,
)
from band.integrations.mcp.backends import create_band_mcp_backend
from band.integrations.mcp.engine import RoomToolResolver
from band.integrations.mcp.local_server import (
    LOCAL_MCP_HTTP_PATH,
    LOCAL_MCP_ROOMS_PATH,
    LOCAL_MCP_SSE_PATH,
    LocalMCPServer,
)
from band.runtime.custom_tools import CustomToolDef
from band.runtime.tools import ToolDefinition

# A valid band_store_memory call, minus any room.
STORE_MEMORY_ARGS: dict[str, Any] = {
    "content": "prefers concise answers",
    "system": "long_term",
    "type": "semantic",
    "segment": "user",
    "thought": "user stated preference",
    "scope": "organization",
}

_TRANSPORT_PATHS: dict[BandMCPTransport, str] = {
    BandMCPTransport.HTTP: LOCAL_MCP_HTTP_PATH,
    BandMCPTransport.SSE: LOCAL_MCP_SSE_PATH,
}


def room_endpoint_path(
    room_id: str, transport: BandMCPTransport = BandMCPTransport.HTTP
) -> str:
    """The URL path a room-bound Band MCP server serves ``room_id`` on."""
    return f"{LOCAL_MCP_ROOMS_PATH}/{room_id}{_TRANSPORT_PATHS[transport]}"


@asynccontextmanager
async def started_backend(
    *,
    room_bound: bool,
    tool_definitions: Sequence[ToolDefinition],
    get_tools: RoomToolResolver,
    additional_tools: list[CustomToolDef] | None = None,
) -> AsyncIterator[BandMCPBackend]:
    """A Band MCP backend on an OS-assigned port, always stopped on exit."""
    backend = await create_band_mcp_backend(
        BandMCPBackendSettings(
            tool_definitions=tool_definitions,
            get_tools=get_tools,
            additional_tools=additional_tools or (),
            room_bound=room_bound,
            port_min=0,
            port_max=0,
        )
    )
    try:
        yield backend
    finally:
        await backend.stop()


@dataclass
class FakeLocalServer:
    port: int


class FakeBandMCPBackend:
    """A ``BandMCPBackend`` stand-in for tests that never dial it.

    Each fake gets its own port, so a replaced backend shows up as a changed
    URL. ``stop`` can be held open with ``stop_release`` to exercise a slow
    shutdown.
    """

    _ports = itertools.count(50000)

    def __init__(
        self,
        *,
        stop_started: asyncio.Event | None = None,
        stop_release: asyncio.Event | None = None,
    ) -> None:
        self.allowed_tools: list[str] = []
        self.local_server = FakeLocalServer(port=next(self._ports))
        self.is_running = True
        self.stop_calls = 0
        self._stop_started = stop_started
        self._stop_release = stop_release

    def endpoint(self, transport: BandMCPTransport, room_id: str | None = None) -> str:
        path = (
            _TRANSPORT_PATHS[transport]
            if room_id is None
            else room_endpoint_path(room_id, transport)
        )
        return f"http://127.0.0.1:{self.local_server.port}{path}"

    async def stop(self) -> None:
        self.stop_calls += 1
        self.is_running = False
        if self._stop_started is not None:
            self._stop_started.set()
        if self._stop_release is not None:
            await self._stop_release.wait()


class BackendStarts:
    """Stands in for ``create_band_mcp_backend``, recording the settings and
    the avoided port each start asked for.

    Starts are answered from ``outcomes`` in order -- a backend is returned,
    an exception raised -- and then by ``then`` (sync or async), or fail when
    ``then`` is None.
    """

    def __init__(self, outcomes: Sequence[Any], then: Callable[[], Any] | None) -> None:
        self._outcomes = list(outcomes)
        self._then = then
        self.requested: list[BandMCPBackendSettings] = []
        self.avoided: list[int | None] = []

    async def __call__(
        self, settings: BandMCPBackendSettings, *, avoid_port: int | None = None
    ) -> Any:
        self.requested.append(settings)
        self.avoided.append(avoid_port)
        if self._outcomes:
            outcome = self._outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        if self._then is None:
            raise AssertionError("unexpected Band MCP backend start")
        backend = self._then()
        return await backend if inspect.isawaitable(backend) else backend


@contextmanager
def backends_created_by(
    *outcomes: Any, then: Callable[[], Any] | None = FakeBandMCPBackend
) -> Iterator[BackendStarts]:
    """Every Band MCP backend start, faked at the one seam all owners use."""
    starts = BackendStarts(outcomes, then)
    with patch("band.integrations.mcp.backends.create_band_mcp_backend", starts):
        yield starts


async def hold_backend(owner: SharedBandMCPBackend, backend: Any = None) -> Any:
    """Have ``owner`` hold ``backend`` (a fresh fake by default) as though it
    had started it."""
    backend = backend or FakeBandMCPBackend()
    with backends_created_by(backend):
        await owner.ensure()
    return backend


async def crash_server(server: LocalMCPServer) -> None:
    """End ``server``'s serve task the way a crash does: on its own, leaving
    its port and socket behind for whoever still holds its URL."""
    uvicorn_server, serve_task = server._uvicorn_server, server._serve_task
    assert uvicorn_server is not None and serve_task is not None, "not running"
    uvicorn_server.should_exit = True
    await asyncio.wait([serve_task])


async def crash_backend(owner: SharedBandMCPBackend) -> BandMCPBackend:
    """Crash the server ``owner`` holds now, returning the crashed backend."""
    backend = owner.current
    assert backend is not None, "no Band MCP backend started"
    await crash_server(backend.local_server)
    return backend


@asynccontextmanager
async def mcp_session(
    url: str, transport: BandMCPTransport = BandMCPTransport.HTTP
) -> AsyncIterator[ClientSession]:
    """An initialized MCP client session to ``url``, closed on exit."""
    streams: AbstractAsyncContextManager[tuple[Any, ...]]
    match transport:
        case BandMCPTransport.HTTP:
            streams = streamable_http_client(url)
        case BandMCPTransport.SSE:
            streams = sse_client(url)
    async with (
        streams as (read_stream, write_stream, *_),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield session


async def served_tool_names(
    url: str, transport: BandMCPTransport = BandMCPTransport.HTTP
) -> set[str]:
    """The names of the tools the Band MCP server at ``url`` lists."""
    async with mcp_session(url, transport) as session:
        return {tool.name for tool in (await session.list_tools()).tools}


def tool_arguments(tools: Sequence[Tool], tool_name: str) -> set[str]:
    """The argument names ``tool_name``'s listed input schema advertises."""
    tool = next(tool for tool in tools if tool.name == tool_name)
    properties: dict[str, Any] = tool.inputSchema.get("properties", {})
    return set(properties)


async def advertised_arguments(session: ClientSession, tool_name: str) -> set[str]:
    """The argument names ``session``'s server advertises for ``tool_name``."""
    return tool_arguments((await session.list_tools()).tools, tool_name)
