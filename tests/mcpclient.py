"""Real MCP client sessions for tests that dial a running Band MCP server."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Tool

from band.integrations.mcp import BandMCPBackendKind

# A valid band_store_memory call, minus any room.
STORE_MEMORY_ARGS: dict[str, Any] = {
    "content": "prefers concise answers",
    "system": "long_term",
    "type": "semantic",
    "segment": "user",
    "thought": "user stated preference",
    "scope": "organization",
}


@asynccontextmanager
async def mcp_session(
    url: str, transport: BandMCPBackendKind = "http"
) -> AsyncIterator[ClientSession]:
    """An initialized MCP client session to ``url``, closed on exit."""
    match transport:
        case "http":
            async with (
                streamable_http_client(url) as (read_stream, write_stream, _),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                yield session
        case "sse":
            async with (
                sse_client(url) as (read_stream, write_stream),
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
