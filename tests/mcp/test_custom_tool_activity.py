from __future__ import annotations

import asyncio

from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel

from band.integrations.mcp.engine import (
    CustomToolSpec,
    EngineSpec,
    build_custom_tool_registration,
    build_engine,
)
from band.testing.activity import ActivityRecorder, shown_tool
from band.testing.fake_tools import FakeAgentTools


class LookupInput(BaseModel):
    """Look up a word."""

    word: str


class TestCustomToolActivity:
    async def test_a_room_bound_custom_tool_shows_on_its_room_until_it_returns(
        self,
    ) -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def lookup(inp: LookupInput) -> str:
            entered.set()
            await release.wait()
            return f"{inp.word}: a definition"

        asked, other = ActivityRecorder(), ActivityRecorder()
        rooms = {
            "room-1": other.observing(FakeAgentTools(room_id="room-1")),
            "room-2": asked.observing(FakeAgentTools(room_id="room-2")),
        }
        registration = build_custom_tool_registration(
            CustomToolSpec(input_model=LookupInput, handler=lookup),
            get_tools=rooms.get,
        )
        server = build_engine(EngineSpec(name="lookup", tools=(registration,)))

        async with create_connected_server_and_client_session(server) as session:
            call = asyncio.create_task(
                session.call_tool("lookup", {"word": "activity", "chat_id": "room-2"})
            )
            await entered.wait()
            assert asked.events == [shown_tool("lookup")]
            release.set()
            result = await call

        assert not result.isError, result.content
        asked.assert_completed_tool("lookup")
        assert other.events == []
