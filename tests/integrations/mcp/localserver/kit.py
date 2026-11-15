"""Kit for the local MCP server tests: an echo tool, its registration, and
the checks a session or a stopped server is held to."""

from __future__ import annotations

import json

from mcp import ClientSession
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from band.integrations.mcp.engine import MCPToolRegistration
from band.integrations.mcp.local_server import LocalMCPServer

AUTH_TOKEN = "test-local-mcp-token"


class EchoInput(BaseModel):
    """Echo text back to the caller."""

    message: str


async def echo_tool(input_data: EchoInput) -> dict[str, str]:
    return {"echo": input_data.message}


def text_of(result: CallToolResult) -> str:
    """Narrow the first content block to `TextContent` and return its text."""
    block = result.content[0]
    assert isinstance(block, TextContent), block
    return block.text


def registration_named(
    registrations: list[MCPToolRegistration], name: str
) -> MCPToolRegistration:
    return next(item for item in registrations if item.name == name)


def echo_tool_registration() -> MCPToolRegistration:
    # A registration's execute() always returns a wire-serialized string: the
    # dynamic handler build_engine() creates always declares -> str, so
    # FastMCP's structured-output validation rejects a raw dict here.
    async def execute(arguments: dict[str, str]) -> str:
        return json.dumps({"echo": arguments["message"]})

    return MCPToolRegistration(
        name="echo",
        description="Echo a message",
        input_model=EchoInput,
        execute=execute,
    )


async def session_lists_only_echo(session: ClientSession) -> None:
    tools_result = await session.list_tools()
    assert [tool.name for tool in tools_result.tools] == ["echo"]


async def call_echo(session: ClientSession, message: str) -> None:
    result = await session.call_tool("echo", {"message": message})
    assert not result.isError
    assert json.loads(text_of(result)) == {"echo": message}


def assert_fully_stopped(server: LocalMCPServer) -> None:
    assert server._serve_task is None
    assert server._socket is None
    assert server._uvicorn_server is None
