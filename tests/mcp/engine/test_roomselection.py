"""MCP roomselection behavior."""

from __future__ import annotations

import json

import httpx
import pytest
from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.context import RequestContext
from mcp.shared.memory import create_connected_server_and_client_session
from starlette.requests import Request

from band.integrations.mcp.engine import (
    EmbeddedResolver,
    EngineSpec,
    build_engine,
    build_tool_registration,
    extend_with_chat_id,
    pin_existing_chat_id,
)
from band.runtime.tools import TOOL_DEFINITIONS, AgentTools
from tests.identifiers import UUID_ID
from tests.runtime.helpers import memory_client, rest_client_over


class TestRoomSelection:
    @pytest.mark.parametrize("field", ["chat_id", "room_id"])
    @pytest.mark.parametrize("room", ["", ".", "#", "../chats/"])
    @pytest.mark.parametrize("pinned", [False, True])
    @pytest.mark.asyncio
    async def test_room_ids_validate_effective_selection(
        self, field: str, room: str, pinned: bool
    ) -> None:
        requests: list[httpx.Request] = []

        def answer(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200, json={"data": {"chat_room_id": "room-1", "goal_title": "ship"}}
            )

        async with rest_client_over(answer) as rest:
            tools = AgentTools("room-1", rest)
            definition = TOOL_DEFINITIONS["band_get_board"]
            model = (
                pin_existing_chat_id(definition.input_model)
                if pinned
                else extend_with_chat_id(definition.input_model, None)
            )
            registration = build_tool_registration(
                definition,
                input_model=model,
                resolver=EmbeddedResolver(get_tools=lambda room: tools),
                strip_chat_id=True,
                pinned_room_id="room-1" if pinned else None,
            )
            mcp = build_engine(
                EngineSpec(name="room-validation", tools=(registration,))
            )
            async with create_connected_server_and_client_session(
                mcp._mcp_server
            ) as session:
                result = await session.call_tool(definition.name, {field: room})
                assert bool(result.isError) is not pinned
                if pinned:
                    assert [q.url.path for q in requests] == [
                        "/api/v1/agent/chats/room-1/board"
                    ]
                else:
                    assert (
                        "chat_id" in result.content[0].text
                        or "room_id" in result.content[0].text
                    )
                    assert requests == []

    @pytest.mark.parametrize("field", ["chat_id", "room_id"])
    @pytest.mark.parametrize("endpoint_room", ["room-1", "."])
    @pytest.mark.asyncio
    async def test_connection_room_overrides_discarded_client_input(
        self, field: str, endpoint_room: str
    ) -> None:
        async with memory_client() as (rest, requests):
            tools = AgentTools("room-1", rest)
            definition = TOOL_DEFINITIONS["band_get_memory"]
            registration = build_tool_registration(
                definition,
                pin_existing_chat_id(definition.input_model),
                resolver=EmbeddedResolver(get_tools=lambda room: tools),
                strip_chat_id=True,
                room_from_connection=True,
            )
            mcp = build_engine(
                EngineSpec(name="connection-room-validation", tools=(registration,))
            )
            context = Context(
                fastmcp=mcp,
                request_context=RequestContext(
                    request_id="1",
                    meta=None,
                    session=None,
                    lifespan_context=None,
                    request=Request(
                        {"type": "http", "path_params": {"room_id": endpoint_room}}
                    ),
                ),
            )
            tool = mcp._tool_manager.get_tool(definition.name)
            if endpoint_room == "room-1":
                result = await tool.run(
                    {field: "#", "memory_id": UUID_ID}, context=context
                )
                assert json.loads(result)["id"] == UUID_ID
                assert [q.url.path for q in requests] == [
                    f"/api/v1/agent/memories/{UUID_ID}"
                ]
            else:
                with pytest.raises(ToolError, match="chat_id"):
                    await tool.run(
                        {field: "room-1", "memory_id": UUID_ID}, context=context
                    )
                assert requests == []
