"""Kit for the standalone door: a real MCP session on the CLI door's engine
and the payloads of the tool calls made through it."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from band_mcp.config import Config, Scope
from band_mcp.server import standalone_spec
from band_mcp.shared import build_standalone_resolver
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel

from band.client.rest import aclose_rest_client
from band.integrations.mcp.engine import build_engine
from band.runtime.tools.schema import serialize_tool_result
from tests.mcp.standalonedoor.peer import AGENT_KEY, USER_KEY


@asynccontextmanager
async def standalone_door() -> AsyncIterator[ClientSession]:
    """The CLI door serving both scopes, as ``band-mcp`` builds it at startup.

    A context manager rather than a fixture: the in-memory session's task
    group must open and close in the test's own task.
    """
    config = Config(
        agent_key=AGENT_KEY,
        user_key=USER_KEY,
        scope=[Scope.AGENT, Scope.HUMAN],
        tools=[],
    )
    resolver = build_standalone_resolver(config)
    engine = build_engine(standalone_spec(config, resolver))
    try:
        async with create_connected_server_and_client_session(
            engine._mcp_server
        ) as session:
            yield session
    finally:
        for rest in (resolver.agent_rest, resolver.human_rest):
            if rest is not None:
                await aclose_rest_client(rest)


def read_as(model: type[BaseModel], wire: dict[str, Any]) -> Any:
    """A platform body as a tool returns it: read into its Fern model, then
    serialized the one way every tool result is."""
    return serialize_tool_result(model.model_validate(wire))


async def call(door: ClientSession, name: str, **arguments: Any) -> Any:
    """A successful tool call's JSON payload, or its text when not JSON."""
    result = await door.call_tool(name, arguments)
    assert not result.isError, result.content
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


async def refusal(door: ClientSession, name: str, **arguments: Any) -> str:
    """A failed tool call's error text."""
    result = await door.call_tool(name, arguments)
    assert result.isError, result.content
    return result.content[0].text
