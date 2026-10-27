"""Real Band MCP servers and client sessions for tests that dial them."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
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
    LOCAL_MCP_AUTH_HEADER,
    LOCAL_MCP_AUTH_SCHEME,
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

# The MCP SDK's own client timeouts: a long read keeps an idle stream open.
MCP_CLIENT_TIMEOUT = httpx.Timeout(30.0, read=300.0)

_TRANSPORT_PATHS: dict[BandMCPTransport, str] = {
    BandMCPTransport.HTTP: LOCAL_MCP_HTTP_PATH,
    BandMCPTransport.SSE: LOCAL_MCP_SSE_PATH,
}


def bearer_token(headers: Mapping[str, str]) -> str:
    """The bearer token a Band MCP server's ``headers`` present."""
    scheme, _, token = headers[LOCAL_MCP_AUTH_HEADER].partition(" ")
    assert scheme == LOCAL_MCP_AUTH_SCHEME, scheme
    return token


def endpoint_path(
    transport: BandMCPTransport = BandMCPTransport.HTTP, room_id: str | None = None
) -> str:
    """The URL path a Band MCP server serves ``transport`` on: the multi-room
    one, or ``room_id``'s on a room-bound server."""
    if room_id is None:
        return _TRANSPORT_PATHS[transport]
    return f"{LOCAL_MCP_ROOMS_PATH}/{room_id}{_TRANSPORT_PATHS[transport]}"


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
async def _client_streams(
    url: str, transport: BandMCPTransport, headers: Mapping[str, str]
) -> AsyncIterator[tuple[Any, ...]]:
    """The read/write streams of an MCP client dialing ``url`` with ``headers``."""
    match transport:
        case BandMCPTransport.HTTP:
            async with (
                httpx.AsyncClient(
                    headers=dict(headers), timeout=MCP_CLIENT_TIMEOUT
                ) as http,
                streamable_http_client(url, http_client=http) as streams,
            ):
                yield streams
        case BandMCPTransport.SSE:
            async with sse_client(url, headers=dict(headers)) as streams:
                yield streams


@asynccontextmanager
async def mcp_session(
    url: str,
    transport: BandMCPTransport = BandMCPTransport.HTTP,
    *,
    headers: Mapping[str, str],
) -> AsyncIterator[ClientSession]:
    """An initialized MCP client session to ``url`` sending ``headers`` with
    every request, closed on exit."""
    async with (
        _client_streams(url, transport, headers) as (read_stream, write_stream, *_),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield session


def backend_session(
    backend: BandMCPBackend,
    transport: BandMCPTransport = BandMCPTransport.HTTP,
    room_id: str | None = None,
) -> AbstractAsyncContextManager[ClientSession]:
    """A session to ``backend``'s endpoint, sending the headers it requires."""
    return mcp_session(
        backend.endpoint(transport, room_id), transport, headers=backend.headers
    )


def advertised_server(servers: Sequence[Any], name: str) -> Any:
    """The MCP server config named ``name`` among a session's advertised ones."""
    config = next(
        (config for config in servers if getattr(config, "name", None) == name), None
    )
    if config is None:
        raise ValueError(f"MCP server {name!r} was not advertised")
    return config


def advertised_headers(config: Any) -> dict[str, str]:
    """The headers an advertised ACP MCP server config tells the agent to send."""
    return {header.name: header.value for header in config.headers}


def advertised_session(config: Any) -> AbstractAsyncContextManager[ClientSession]:
    """A session to an advertised ACP MCP server config, dialed as told."""
    return mcp_session(
        config.url, BandMCPTransport(config.type), headers=advertised_headers(config)
    )


async def call_advertised_tool(
    config: Any, tool_name: str, arguments: dict[str, Any]
) -> Any:
    """Call ``tool_name`` on an advertised MCP server config."""
    async with advertised_session(config) as client:
        result = await client.call_tool(tool_name, arguments)
    if result.isError:
        raise RuntimeError(f"MCP tool {tool_name!r} failed: {result.content}")
    return result.content


async def served_tool_names(
    url: str,
    transport: BandMCPTransport = BandMCPTransport.HTTP,
    *,
    headers: Mapping[str, str],
) -> set[str]:
    """The names of the tools the Band MCP server at ``url`` lists."""
    async with mcp_session(url, transport, headers=headers) as session:
        return {tool.name for tool in (await session.list_tools()).tools}


def tool_arguments(tools: Sequence[Tool], tool_name: str) -> set[str]:
    """The argument names ``tool_name``'s listed input schema advertises."""
    tool = next(tool for tool in tools if tool.name == tool_name)
    properties: dict[str, Any] = tool.inputSchema.get("properties", {})
    return set(properties)


async def advertised_arguments(session: ClientSession, tool_name: str) -> set[str]:
    """The argument names ``session``'s server advertises for ``tool_name``."""
    return tool_arguments((await session.list_tools()).tools, tool_name)
