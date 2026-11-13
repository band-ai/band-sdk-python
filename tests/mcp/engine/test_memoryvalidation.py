"""MCP memoryvalidation behavior."""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from band.integrations.mcp.engine import (
    EmbeddedResolver,
    EngineSpec,
    build_engine,
    build_tool_registration,
    pin_existing_chat_id,
)
from band.runtime.tools import TOOL_DEFINITIONS, AgentTools, HumanTools
from tests.identifiers import INVALID_IDS, UUID_ID
from tests.mcp.engine.peers import HumanResolver
from tests.mcp.engine.protocol import _call, _list_tool
from tests.runtime.helpers import memory_client


class TestMemoryValidation:
    @pytest.mark.parametrize(
        "name,human",
        [
            ("band_get_memory", False),
            ("band_get_user_memory", True),
            ("band_archive_user_memory", True),
        ],
    )
    @pytest.mark.parametrize("memory_id", INVALID_IDS)
    @pytest.mark.asyncio
    async def test_memory_id_rejected_over_mcp_before_http(
        self, name: str, human: bool, memory_id: str
    ) -> None:
        async with memory_client() as (rest, requests):
            tools = HumanTools(rest) if human else AgentTools("room-1", rest)
            resolver = (
                HumanResolver(tools)
                if human
                else EmbeddedResolver(get_tools=lambda room: tools)
            )
            definition = TOOL_DEFINITIONS[name]
            registration = build_tool_registration(
                definition,
                input_model=definition.input_model
                if human
                else pin_existing_chat_id(definition.input_model),
                resolver=resolver,
                strip_chat_id=not human,
                pinned_room_id=None if human else "room-1",
            )
            mcp = build_engine(
                EngineSpec(name="memory-validation", tools=(registration,))
            )
            async with create_connected_server_and_client_session(
                mcp._mcp_server
            ) as session:
                result = await session.call_tool(name, {"memory_id": memory_id})
                assert result.isError
                assert "memory_id" in result.content[0].text
                assert requests == []

    @pytest.mark.parametrize(
        "name,human,method,suffix",
        [
            ("band_get_memory", False, "GET", ""),
            ("band_get_user_memory", True, "GET", ""),
            ("band_archive_user_memory", True, "POST", "/archive"),
        ],
    )
    @pytest.mark.asyncio
    async def test_memory_item_survives_real_mcp_and_rest(
        self, name: str, human: bool, method: str, suffix: str
    ) -> None:
        async with memory_client() as (rest, requests):
            tools = HumanTools(rest) if human else AgentTools("room-1", rest)
            resolver = (
                HumanResolver(tools)
                if human
                else EmbeddedResolver(get_tools=lambda room: tools)
            )
            definition = TOOL_DEFINITIONS[name]
            registration = build_tool_registration(
                definition,
                input_model=definition.input_model
                if human
                else pin_existing_chat_id(definition.input_model),
                resolver=resolver,
                strip_chat_id=not human,
                pinned_room_id=None if human else "room-1",
            )
            mcp = build_engine(
                EngineSpec(name="memory-validation", tools=(registration,))
            )
            async with create_connected_server_and_client_session(
                mcp._mcp_server
            ) as session:
                advertised = await _list_tool(session, name)
                field = advertised.inputSchema["properties"]["memory_id"]
                assert field["type"] == "string"
                assert (
                    field["description"]
                    == definition.input_model.model_fields["memory_id"].description
                )
                assert "memory_id" in advertised.inputSchema["required"]
                assert "pattern" not in field and "minLength" not in field
                result = await _call(session, name, memory_id=UUID_ID)
                payload = result["data"] if human else result
                assert payload["id"] == UUID_ID
                assert payload["content"] == "remember this"
                surface = "me" if human else "agent"
                assert [(q.method, q.url.path) for q in requests] == [
                    (method, f"/api/v1/{surface}/memories/{UUID_ID}{suffix}")
                ]
