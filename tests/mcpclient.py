"""Real Band MCP servers and client sessions for tests that dial them."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

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


def endpoint_path(
    transport: BandMCPTransport = BandMCPTransport.HTTP, room_id: str | None = None
) -> str:
    """The URL path a Band MCP server serves ``transport`` on: the multi-room
    one, or ``room_id``'s on a room-bound server."""
    if room_id is None:
        return _TRANSPORT_PATHS[transport]
    return f"{LOCAL_MCP_ROOMS_PATH}/{room_id}{_TRANSPORT_PATHS[transport]}"


def room_endpoint_path(
    room_id: str, transport: BandMCPTransport = BandMCPTransport.HTTP
) -> str:
    """The URL path a room-bound Band MCP server serves ``room_id`` on."""
    return endpoint_path(transport, room_id)


@asynccontextmanager
async def started_backend(
    *,
    room_bound: bool,
    tool_definitions: Sequence[ToolDefinition],
    get_tools: RoomToolResolver,
    additional_tools: Sequence[CustomToolDef] = (),
) -> AsyncIterator[BandMCPBackend]:
    """A Band MCP backend on an OS-assigned port, always stopped on exit."""
    settings = BandMCPBackendSettings(
        tool_definitions=tool_definitions,
        get_tools=get_tools,
        additional_tools=additional_tools,
        room_bound=room_bound,
        port_min=0,
        port_max=0,
    )
    async with SharedBandMCPBackend(lambda: settings) as owner:
        yield await owner.ensure()


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
