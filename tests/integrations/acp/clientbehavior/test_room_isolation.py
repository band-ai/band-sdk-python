from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from band.core.types import Capability
from band.runtime.tools import BAND_MCP_SERVER_NAME
from tests.integrations.acp.acp_toolkit import (
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.clientbehavior.kit import (
    band_mcp_url,
)
from tests.mcpclient import (
    STORE_MEMORY_ARGS,
    endpoint_path,
    tool_arguments,
)


class TestRoomIsolation:
    @pytest.mark.asyncio
    async def test_two_rooms_get_isolated_sessions(self, fake_agent) -> None:
        @fake_agent.on_prompt
        async def _reply(agent, session_id: str) -> None:
            await agent.say(session_id, f"reply for {session_id}")

        async with acp_adapter(fake_agent) as session:
            reply1 = await session.send("hi", room="room-1")
            reply2 = await session.send("hi", room="room-2")
            assert session.session_id("room-1") != session.session_id("room-2")

        # Each room created its own ACP session and got its own reply — no cross-talk.
        assert len({s["session_id"] for s in fake_agent.sessions}) == 2
        assert reply1.thoughts != reply2.thoughts

    @pytest.mark.asyncio
    async def test_rooms_get_their_own_band_mcp_endpoint_on_one_server(
        self,
        fake_agent,
    ) -> None:
        fake_agent.will_say("ok")

        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            await session.send("hi", room="room-1")
            await session.send("hi", room="room-2")
            urls = [
                urlsplit(band_mcp_url(fake_agent, session.session_id(room)))
                for room in ("room-1", "room-2")
            ]

        assert len({url.netloc for url in urls}) == 1
        assert [url.path for url in urls] == [
            endpoint_path(room_id="room-1"),
            endpoint_path(room_id="room-2"),
        ]

    @pytest.mark.asyncio
    async def test_injected_band_tools_advertise_no_chat_id(self, fake_agent) -> None:
        fake_agent.will_say("ok")

        async with acp_adapter(
            fake_agent,
            fake_agent_config(inject_band_tools=True),
            capabilities=Capability.MEMORY,
        ) as session:
            await session.send("hi", room="room-1")
            tools = await fake_agent.list_mcp_tools(
                session_id=session.session_id("room-1"), server=BAND_MCP_SERVER_NAME
            )

        assert "chat_id" not in tool_arguments(tools, "band_store_memory")

    @pytest.mark.asyncio
    async def test_band_tool_call_without_chat_id_lands_in_its_own_room(
        self,
        fake_agent,
    ) -> None:
        fake_agent.will_call_mcp_tool(
            "tc-memory", "band_store_memory", arguments=STORE_MEMORY_ARGS
        ).will_say("stored")

        async with acp_adapter(
            fake_agent,
            fake_agent_config(inject_band_tools=True),
            capabilities=Capability.MEMORY,
        ) as session:
            room1 = await session.send("remember this", room="room-1")
            room2 = await session.send("remember this", room="room-2")

        assert [len(room1.memories), len(room2.memories)] == [1, 1]
