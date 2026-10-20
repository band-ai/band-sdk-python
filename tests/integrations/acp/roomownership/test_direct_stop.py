"""A direct stop owns the MCP server close until it is confirmed."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import RoomClosingError
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.roomownership.kit import (
    TEST_GUARD_S,
    restart,
)
from tests.mcpbackends import FakeBandMCPBackend, backends_created_by


class TestDirectStop:
    """A direct stop owns the MCP server's close until it is confirmed; only
    a restart serves turns again."""

    @pytest.mark.asyncio
    async def test_a_cancelled_stop_keeps_closing_the_server_until_a_retry(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        stopped, closing = asyncio.Event(), asyncio.Event()
        backend = FakeBandMCPBackend(stop_started=closing, stop_release=stopped)
        config = fake_agent_config(inject_band_tools=True)
        with backends_created_by(backend) as starts:
            async with acp_adapter(agent, config) as session:
                try:
                    await session.send("hello")
                    stopping = asyncio.create_task(session.adapter.stop())
                    await closing.wait()
                    stopping.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await stopping
                    assert backend.stop_calls == 1
                    assert len(starts.requested) == 1
                finally:
                    stopped.set()
                await session.adapter.stop()
                await restart(session)
                reply = await session.send("again")

        assert backend.stop_calls == 1
        assert len(starts.requested) == 2
        assert reply.errors == []

    @pytest.mark.asyncio
    async def test_a_failed_close_is_retried_by_the_next_stop(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        backend = FakeBandMCPBackend()
        backend.fail_stops = 1
        config = fake_agent_config(inject_band_tools=True)
        with backends_created_by(backend) as starts:
            async with acp_adapter(agent, config) as session:
                await session.send("hello")
                with pytest.raises(OSError, match="stop failed"):
                    await session.adapter.stop()

                await session.adapter.stop()
                await restart(session)
                reply = await session.send("again")

        assert backend.stop_calls == 2
        assert len(starts.requested) == 2
        assert reply.errors == []

    @pytest.mark.asyncio
    async def test_concurrent_stops_share_one_close(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        stopped, closing = asyncio.Event(), asyncio.Event()
        backend = FakeBandMCPBackend(stop_started=closing, stop_release=stopped)
        config = fake_agent_config(inject_band_tools=True)
        with backends_created_by(backend):
            async with acp_adapter(agent, config) as session:
                await session.send("hello")
                stops = [asyncio.create_task(session.adapter.stop()) for _ in range(2)]
                await closing.wait()
                stopped.set()
                async with asyncio.timeout(TEST_GUARD_S):
                    await asyncio.gather(*stops)

        assert backend.stop_calls == 1

    @pytest.mark.asyncio
    async def test_a_turn_after_a_stop_waits_for_a_restart(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        with backends_created_by() as starts:
            async with acp_adapter(
                agent, fake_agent_config(inject_band_tools=True)
            ) as session:
                await session.send("hello")
                await session.adapter.stop()

                with pytest.raises(RoomClosingError):
                    await session.send("while stopped")
                assert len(starts.requested) == 1

                await restart(session)
                reply = await session.send("again")

        assert len(starts.requested) == 2
        assert reply.errors == []

    @pytest.mark.asyncio
    async def test_a_stop_racing_shutdown_never_reopens_the_server(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        stopped, closing = asyncio.Event(), asyncio.Event()
        backend = FakeBandMCPBackend(stop_started=closing, stop_release=stopped)
        config = fake_agent_config(inject_band_tools=True)
        with backends_created_by(backend) as starts:
            async with acp_adapter(agent, config) as session:
                await session.send("hello")
                shutdown = asyncio.create_task(session.adapter.cleanup_all())
                await closing.wait()
                stopping = asyncio.create_task(session.adapter.stop())
                stopped.set()
                async with asyncio.timeout(TEST_GUARD_S):
                    await asyncio.gather(shutdown, stopping)

                with pytest.raises(RoomClosingError):
                    await session.send("after shutdown")

        assert len(starts.requested) == 1
