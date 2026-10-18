"""Leaving keeps the room owned until its process is confirmed closed."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import RoomClosingError
from tests.adapters.waiting import spin
from tests.integrations.acp.acp_toolkit import (
    DEFAULT_ROOM,
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.integrations.acp.acp_toolkit.agent import AGENT_EXIT_FAILURE
from tests.mcpbackends import FakeBandMCPBackend, backends_created_by
from tests.mcpclient import crash_backend


class TestFinalCleanup:
    """Leaving keeps the room owned until its process is confirmed closed."""

    @pytest.mark.asyncio
    async def test_a_failed_exit_keeps_the_room_owned_until_a_retry_succeeds(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        async with acp_adapter(agent) as session:
            await session.send("hello")
            runtime = session.runtime()
            agent.fails_next_exit()

            with pytest.raises(RuntimeError, match=AGENT_EXIT_FAILURE):
                await session.adapter.on_cleanup(DEFAULT_ROOM)

            room = session.room()
            assert room.runtime is runtime
            assert room.retained.snapshot.pending_cleanup
            with pytest.raises(RoomClosingError):
                await session.send("rejoin")
            assert agent.session_ids() == ["fake-session-1"]

            await session.adapter.on_cleanup(DEFAULT_ROOM)

            assert runtime.closed
            assert DEFAULT_ROOM not in session.adapter._rooms
            await session.send("rejoined")
        assert agent.session_ids() == ["fake-session-1", "fake-session-2"]

    @pytest.mark.asyncio
    async def test_a_cancelled_cleanup_keeps_closing_and_refuses_a_rejoin(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        exiting = agent.exits_slowly()
        async with acp_adapter(agent) as session:
            await session.send("hello")
            runtime = session.runtime()
            cleanup = asyncio.create_task(session.adapter.on_cleanup(DEFAULT_ROOM))
            await exiting.received.wait()

            with pytest.raises(RoomClosingError):
                await session.send("rejoin")
            cleanup.cancel()
            await asyncio.gather(cleanup, return_exceptions=True)
            assert not runtime.closed
            assert DEFAULT_ROOM in session.adapter._rooms

            exiting.release.set()
            await session.adapter.on_cleanup(DEFAULT_ROOM)

            assert runtime.closed
            assert DEFAULT_ROOM not in session.adapter._rooms

    @pytest.mark.asyncio
    async def test_a_held_room_keeps_the_shared_mcp_server_until_a_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        config = fake_agent_config(inject_band_tools=True)
        with backends_created_by():
            async with acp_adapter(agent, config) as session:
                await session.send("hello")
                runtime = session.runtime()
                exiting = agent.exits_slowly()

                cleanup = asyncio.create_task(session.adapter.cleanup_all())
                try:
                    await exiting.received.wait()
                    cleanup.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await cleanup
                    assert session.adapter._mcp.current is not None
                    assert not runtime.closed
                    assert DEFAULT_ROOM in session.adapter._rooms
                    with pytest.raises(RoomClosingError):
                        await session.send("while closing")

                    joined = asyncio.Event()
                    stop_retention = session.adapter._retention.stop

                    async def join_retention() -> None:
                        joined.set()
                        await stop_retention()

                    monkeypatch.setattr(
                        session.adapter._retention, "stop", join_retention
                    )
                    restarting = asyncio.create_task(
                        session.adapter.on_started("Fake Agent", "while closing")
                    )
                    await joined.wait()
                    restarting.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await restarting
                    assert session.adapter._mcp.current is not None
                    assert not runtime.closed
                finally:
                    exiting.release.set()
                await session.adapter.cleanup_all()

                assert runtime.closed
                assert session.adapter._mcp.current is None
                await session.adapter.on_started("Fake Agent", "restarted")

    @pytest.mark.asyncio
    async def test_a_cancelled_mcp_stop_is_left_for_a_retry(self) -> None:
        agent = FakeACPAgent().will_say("ok")
        stopping, closing = asyncio.Event(), asyncio.Event()
        backend = FakeBandMCPBackend(stop_started=closing, stop_release=stopping)
        config = fake_agent_config(inject_band_tools=True)
        with backends_created_by(backend):
            async with acp_adapter(agent, config) as session:
                await session.send("hello")
                runtime = session.runtime()

                cleanup = asyncio.create_task(session.adapter.cleanup_all())
                try:
                    await closing.wait()
                    cleanup.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await cleanup
                    assert runtime.closed
                finally:
                    stopping.set()
                await session.adapter.cleanup_all()

                assert session.adapter._mcp.current is None
                assert backend.stop_calls == 1

    @pytest.mark.asyncio
    async def test_a_failed_room_exit_fails_shutdown_and_keeps_the_mcp_server(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        config = fake_agent_config(inject_band_tools=True)
        with backends_created_by():
            async with acp_adapter(agent, config) as session:
                await session.send("hello")
                runtime = session.runtime()
                agent.fails_next_exit()

                with pytest.RaisesGroup(
                    pytest.RaisesExc(RuntimeError, match=AGENT_EXIT_FAILURE)
                ):
                    await session.adapter.cleanup_all()
                assert session.adapter._mcp.current is not None
                assert not runtime.closed

                await session.adapter.cleanup_all()

                assert runtime.closed
                assert session.adapter._mcp.current is None

    @pytest.mark.asyncio
    async def test_retired_session_closes_finish_before_the_process_exits(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("ok")
        async with acp_adapter(
            agent, fake_agent_config(inject_band_tools=True)
        ) as session:
            await session.send("before the crash")
            retired = session.session_id(DEFAULT_ROOM)
            closing = agent.holds_session_closes()
            exiting = agent.exits_slowly()
            await crash_backend(session.adapter._mcp)
            await session.send("after the crash")
            await closing.received.wait()

            cleanup = asyncio.create_task(session.adapter.on_cleanup(DEFAULT_ROOM))
            await spin()
            exited_under_a_held_close = exiting.received.is_set()
            closing.release.set()
            await exiting.received.wait()

            assert not exited_under_a_held_close
            assert agent.closed_sessions == [retired]
            exiting.release.set()
            await cleanup
