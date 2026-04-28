from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel

from thenvoi.runtime.custom_tools import get_custom_tool_name
from thenvoi.runtime.mcp_server import (
    LOCAL_MCP_HOST,
    MCPToolRegistration,
    LocalMCPServer,
    build_thenvoi_mcp_tool_registrations,
    build_resolved_thenvoi_mcp_tool_registrations,
)
from thenvoi.runtime.tools import AgentTools


class EchoInput(BaseModel):
    """Echo text back to the caller."""

    message: str


async def echo_tool(input_data: EchoInput) -> dict[str, str]:
    return {"echo": input_data.message}


class TestBuildThenvoiMcpToolRegistrations:
    def test_includes_builtin_and_custom_tools(self) -> None:
        agent_tools = AgentTools("room-123", MagicMock(), [])

        registrations = build_thenvoi_mcp_tool_registrations(
            agent_tools,
            additional_tools=[(EchoInput, echo_tool)],
        )

        tool_names = {registration.name for registration in registrations}
        assert "thenvoi_send_message" in tool_names
        assert get_custom_tool_name(EchoInput) in tool_names

    def test_rejects_duplicate_tool_names(self) -> None:
        agent_tools = AgentTools("room-123", MagicMock(), [])

        with pytest.raises(ValueError, match="Duplicate MCP tool names"):
            build_thenvoi_mcp_tool_registrations(
                agent_tools,
                additional_tools=[
                    (EchoInput, echo_tool),
                    (EchoInput, echo_tool),
                ],
            )

    @pytest.mark.asyncio
    async def test_resolved_registrations_require_room_id(self) -> None:
        tools_by_room = {
            "room-123": AgentTools("room-123", MagicMock(), []),
        }
        registrations = build_resolved_thenvoi_mcp_tool_registrations(
            get_tools=tools_by_room.get
        )

        registration = next(
            item for item in registrations if item.name == "thenvoi_get_participants"
        )
        schema = registration.to_mcp_tool().inputSchema

        assert "room_id" in schema["properties"]
        assert "room_id" in schema["required"]

    @pytest.mark.asyncio
    async def test_resolved_registrations_dispatch_by_room_id(self) -> None:
        rest = MagicMock()
        rest.agent_api_participants = MagicMock()
        rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=MagicMock(data=[])
        )

        room_tools = AgentTools("room-123", rest, [])
        registrations = build_resolved_thenvoi_mcp_tool_registrations(
            get_tools={"room-123": room_tools}.get
        )
        registration = next(
            item for item in registrations if item.name == "thenvoi_get_participants"
        )

        await registration.execute({"room_id": "room-123"})

        rest.agent_api_participants.list_agent_chat_participants.assert_awaited_once()


class TestLocalMcpServer:
    @pytest.mark.asyncio
    async def test_serves_sse_tools_on_localhost(self) -> None:
        async def execute(arguments: dict[str, str]) -> dict[str, str]:
            return {"echo": arguments["message"]}

        server = LocalMCPServer(
            name="test-local-mcp",
            tool_registrations=[
                MCPToolRegistration(
                    name="echo",
                    description="Echo a message",
                    input_model=EchoInput,
                    execute=execute,
                )
            ],
            port_min=0,
            port_max=0,
        )

        await server.start()
        try:
            assert server.url.startswith(f"http://{LOCAL_MCP_HOST}:")

            async with sse_client(server.url) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    tools_result = await session.list_tools()
                    assert [tool.name for tool in tools_result.tools] == ["echo"]

                    result = await session.call_tool("echo", {"message": "hello"})
                    assert not result.isError
                    assert result.structuredContent == {"echo": "hello"}
        finally:
            await server.stop()

    # 30s default barely fits on GitHub Actions Python 3.12 runners — the
    # streamable-HTTP loopback initialization spends most of that on uvicorn
    # startup. Bump to 90s to absorb runner I/O variance (test passes in ~1s
    # locally; this only widens the safety margin on CI).
    @pytest.mark.timeout(90)
    @pytest.mark.asyncio
    async def test_serves_streamable_http_tools_on_localhost(self) -> None:
        async def execute(arguments: dict[str, str]) -> dict[str, str]:
            return {"echo": arguments["message"]}

        server = LocalMCPServer(
            name="test-local-mcp-http",
            tool_registrations=[
                MCPToolRegistration(
                    name="echo",
                    description="Echo a message",
                    input_model=EchoInput,
                    execute=execute,
                )
            ],
            port_min=0,
            port_max=0,
        )

        await server.start()
        try:
            assert server.http_url.startswith(f"http://{LOCAL_MCP_HOST}:")

            async with streamablehttp_client(server.http_url) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    tools_result = await session.list_tools()
                    assert [tool.name for tool in tools_result.tools] == ["echo"]

                    result = await session.call_tool("echo", {"message": "hello"})
                    assert not result.isError
                    assert result.structuredContent == {"echo": "hello"}
        finally:
            await server.stop()
