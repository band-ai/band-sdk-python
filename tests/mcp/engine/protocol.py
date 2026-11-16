"""MCP engine protocol support."""

from __future__ import annotations

import json
from typing import Any

from mcp import ClientSession
from mcp.server.fastmcp import FastMCP

from tests.mcp.engine.models import EchoInput


async def _list_tool(session: ClientSession, name: str) -> Any:
    result = await session.list_tools()
    return next((tool for tool in result.tools if tool.name == name), None)


async def _call(session: ClientSession, name: str, **arguments: object) -> Any:
    """Call a tool and parse its text content -- the engine's real wire shape
    (row 15: every registration returns a JSON *string*, its only content)."""
    result = await session.call_tool(name, arguments)
    assert not result.isError, result.content
    text = result.content[0].text if result.content else None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


async def _direct_call(mcp: FastMCP, name: str, **kwargs: object) -> Any:
    """Dispatch straight through ``_tool_manager.call_tool`` -- the engine's
    own entry point, one layer below a ``ClientSession`` round trip. Matches
    ``tests/mcp/test_fake_human_tools.py``'s ``_call`` helper."""
    raw = await mcp._tool_manager.call_tool(name, kwargs)
    assert isinstance(raw, str)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


async def _echo(input_data: EchoInput) -> dict[str, str]:
    return {"echo": input_data.message}
