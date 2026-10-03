"""A room-bound Band MCP server takes each call's room from its endpoint path.

Real ``LocalMCPServer``s and real MCP clients: the room comes from the HTTP
request, which only a real server carries.
"""

from __future__ import annotations

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent
from pydantic import BaseModel

from band.core.types import ALL_CAPABILITIES
from band.integrations.mcp import BandMCPTransport
from band.integrations.mcp.engine import (
    EngineSpec,
    build_engine,
    build_resolved_band_mcp_tool_registrations,
)
from band.runtime.tools import BandTool, iter_tool_definitions
from band.testing import FakeAgentTools
from tests.mcpclient import (
    STORE_MEMORY_ARGS,
    advertised_arguments,
    mcp_session,
    started_backend,
    tool_arguments,
)

ROOM_A = "room-a"
ROOM_B = "room-b"
TRANSPORTS = list(BandMCPTransport)


class LookupInput(BaseModel):
    """Look something up."""

    query: str


async def lookup(input_data: LookupInput) -> str:
    return input_data.query


@pytest.fixture
def rooms() -> dict[str, FakeAgentTools]:
    return {
        ROOM_A: FakeAgentTools(room_id=ROOM_A),
        ROOM_B: FakeAgentTools(room_id=ROOM_B),
    }


def room_backend(rooms: dict[str, FakeAgentTools], *, room_bound: bool):
    return started_backend(
        room_bound=room_bound,
        tool_definitions=list(iter_tool_definitions(capabilities=ALL_CAPABILITIES)),
        get_tools=rooms.get,
        additional_tools=[(LookupInput, lookup)],
    )


@pytest.mark.timeout(90)
@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_room_endpoint_advertises_no_chat_id(
    rooms: dict[str, FakeAgentTools], transport: BandMCPTransport
) -> None:
    async with (
        room_backend(rooms, room_bound=True) as backend,
        mcp_session(backend.endpoint(transport, ROOM_A), transport) as session,
    ):
        tools = (await session.list_tools()).tools

    assert tool_arguments(tools, BandTool.STORE_MEMORY) == set(STORE_MEMORY_ARGS) | {
        "subject_id",
        "metadata",
    }
    assert tool_arguments(tools, "lookup") == {"query"}


@pytest.mark.timeout(90)
@pytest.mark.asyncio
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_call_lands_in_its_endpoints_room(
    rooms: dict[str, FakeAgentTools], transport: BandMCPTransport
) -> None:
    async with (
        room_backend(rooms, room_bound=True) as backend,
        mcp_session(backend.endpoint(transport, ROOM_A), transport) as session,
    ):
        await session.call_tool(BandTool.STORE_MEMORY, STORE_MEMORY_ARGS)
        await session.call_tool(
            BandTool.STORE_MEMORY, {**STORE_MEMORY_ARGS, "chat_id": ROOM_B}
        )

    assert len(rooms[ROOM_A].memories) == 2
    assert rooms[ROOM_B].memories == []


@pytest.mark.timeout(90)
@pytest.mark.asyncio
async def test_rooms_share_one_server_without_crossing(
    rooms: dict[str, FakeAgentTools],
) -> None:
    async with room_backend(rooms, room_bound=True) as backend:
        for room_id in (ROOM_A, ROOM_B, ROOM_B):
            async with mcp_session(
                backend.endpoint(BandMCPTransport.HTTP, room_id)
            ) as session:
                await session.call_tool(BandTool.STORE_MEMORY, STORE_MEMORY_ARGS)

    assert [len(rooms[ROOM_A].memories), len(rooms[ROOM_B].memories)] == [1, 2]


@pytest.mark.timeout(90)
@pytest.mark.asyncio
async def test_multi_room_endpoint_routes_by_chat_id(
    rooms: dict[str, FakeAgentTools],
) -> None:
    async with (
        room_backend(rooms, room_bound=False) as backend,
        mcp_session(backend.endpoint(BandMCPTransport.HTTP)) as session,
    ):
        assert "chat_id" in await advertised_arguments(session, BandTool.STORE_MEMORY)
        await session.call_tool(
            BandTool.STORE_MEMORY, {**STORE_MEMORY_ARGS, "chat_id": ROOM_B}
        )

    assert rooms[ROOM_A].memories == []
    assert len(rooms[ROOM_B].memories) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("room_bound", [True, False])
async def test_endpoint_of_the_other_kind_raises(
    rooms: dict[str, FakeAgentTools], room_bound: bool
) -> None:
    other_kind_room = None if room_bound else ROOM_A
    expected = "room-bound" if room_bound else "multi-room"
    async with room_backend(rooms, room_bound=room_bound) as backend:
        for transport in TRANSPORTS:
            with pytest.raises(ValueError, match=expected):
                backend.endpoint(transport, other_kind_room)


@pytest.mark.asyncio
async def test_room_bound_tool_without_an_http_request_fails_clearly(
    rooms: dict[str, FakeAgentTools],
) -> None:
    registrations = build_resolved_band_mcp_tool_registrations(
        get_tools=rooms.get,
        capabilities=ALL_CAPABILITIES,
        room_from_connection=True,
    )
    mcp = build_engine(EngineSpec(name="band", tools=tuple(registrations)))

    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool(
            BandTool.STORE_MEMORY, {**STORE_MEMORY_ARGS, "chat_id": ROOM_A}
        )

    assert result.isError
    block = result.content[0]
    assert isinstance(block, TextContent)
    assert "room-bound MCP endpoint" in block.text
    assert rooms[ROOM_A].memories == []
