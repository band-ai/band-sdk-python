"""Real Band MCP servers and client sessions for tests that dial them."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Tool

from band.integrations.mcp import (
    BandMCPBackend,
    BandMCPTransport,
    create_band_mcp_backend,
)
from band.integrations.mcp.engine import RoomToolResolver
from band.integrations.mcp.local_server import (
    LOCAL_MCP_HTTP_PATH,
    LOCAL_MCP_ROOMS_PATH,
    LOCAL_MCP_SSE_PATH,
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
        tool_definitions=tool_definitions,
        get_tools=get_tools,
        additional_tools=additional_tools,
        room_bound=room_bound,
        port_min=0,
        port_max=0,
    )
    try:
        yield backend
    finally:
        await backend.stop()


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


def tool_arguments(tools: Sequence[Tool], tool_name: str) -> set[str]:
    """The argument names ``tool_name``'s listed input schema advertises."""
    tool = next(tool for tool in tools if tool.name == tool_name)
    properties: dict[str, Any] = tool.inputSchema.get("properties", {})
    return set(properties)


async def advertised_arguments(session: ClientSession, tool_name: str) -> set[str]:
    """The argument names ``session``'s server advertises for ``tool_name``."""
    return tool_arguments((await session.list_tools()).tools, tool_name)
