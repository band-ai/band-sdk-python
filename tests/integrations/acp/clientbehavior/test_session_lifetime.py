from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from band.core.exceptions import InputNotAccepted
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.clientbehavior.kit import (
    band_mcp_url,
    rehydration_history,
)
from tests.mcpbackends import backends_created_by
from tests.mcpclient import (
    crash_backend,
    endpoint_path,
)


class TestSessionLifetime:
    @pytest.mark.asyncio
    async def test_a_turn_after_shutdown_is_refused(self, fake_agent) -> None:
        """``cleanup_all()`` (as ``Agent.stop()`` calls it) stops room residency
        and the Band MCP backend for good: a late turn is refused before it starts
        a process or a server nothing would stop."""
        fake_agent.will_say("ok")
        with backends_created_by() as starts:
            async with acp_adapter(
                fake_agent, fake_agent_config(inject_band_tools=True)
            ) as session:
                await session.send("before shutdown", room="room-1")
                await session.adapter.cleanup_all()

                with pytest.raises(InputNotAccepted, match="retention is stopping"):
                    await session.send("after shutdown", room="room-2")

        assert len(starts.requested) == 1
        assert fake_agent.session_ids() == ["fake-session-1"]

    @pytest.mark.asyncio
    async def test_a_room_keeps_its_session_while_its_band_server_lives(
        self,
        fake_agent,
    ) -> None:
        with backends_created_by():
            async with acp_adapter(
                fake_agent, fake_agent_config(inject_band_tools=True)
            ) as session:
                await session.send("first", room="room-1")
                first = session.session_id("room-1")
                await session.send("second", room="room-1")

                assert session.session_id("room-1") == first

    @pytest.mark.asyncio
    async def test_an_open_rooms_band_reply_survives_a_crash(self, fake_agent) -> None:
        """The room's open session still dials the crashed server's port; the next
        message moves the room to a fresh session on its replacement."""
        fake_agent.will_call_mcp_tool(
            "tc-message",
            "band_send_message",
            arguments={"content": "Reply from the agent", "mentions": ["@pat"]},
        )

        async with acp_adapter(
            fake_agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            await session.send("before the crash", room="room-1")
            stale_session = session.session_id("room-1")
            crashed_url = band_mcp_url(fake_agent, stale_session)
            await crash_backend(session.adapter._mcp)

            reply = await session.send("after the crash", room="room-1")
            live_url = band_mcp_url(fake_agent, session.session_id("room-1"))
            await session.retired_sessions_closed("room-1")

            assert fake_agent.closed_sessions == [stale_session]
            assert not session.runtime_keeps_output_of(stale_session, room="room-1")

        assert reply.texts == ["Reply from the agent"]
        assert "error" not in reply.outline
        assert urlsplit(live_url).port != urlsplit(crashed_url).port

    @pytest.mark.asyncio
    async def test_reloaded_session_gets_its_rooms_band_mcp_endpoint(
        self,
    ) -> None:
        agent = (
            FakeACPAgent(supports_session_load=True)
            .knows_session("persisted")
            .will_say("ok")
            .will_say("still here")
        )

        async with acp_adapter(
            agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            await session.send(
                "hi",
                bootstrap=True,
                history=rehydration_history(session="persisted"),
            )
            await session.send("again")

            assert session.session_id("room-1") == "persisted"

        assert agent.session_load_requests == ["persisted"]
        assert urlsplit(band_mcp_url(agent, "persisted")).path == endpoint_path(
            room_id="room-1"
        )

    @pytest.mark.asyncio
    async def test_a_bootstrap_after_a_crash_never_restores_the_retired_session(
        self,
    ) -> None:
        """Each bootstrap names the room's persisted session (as a one-shot
        invoker sends it); once its Band URL went stale, the room gets a fresh
        session rather than a reload of the one being closed."""
        agent = FakeACPAgent(supports_session_load=True).knows_session("persisted")
        history = rehydration_history(session="persisted")

        async with acp_adapter(
            agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            await session.send("before the crash", bootstrap=True, history=history)
            await crash_backend(session.adapter._mcp)
            await session.send("after the crash", bootstrap=True, history=history)
            await session.retired_sessions_closed("room-1")

            assert session.session_id("room-1") != "persisted"

        assert agent.session_load_requests == ["persisted"]
        assert agent.closed_sessions == ["persisted"]
