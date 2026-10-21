"""A completed transport shutdown is not a cancelled, still-live waiter."""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock

import pytest

from band.client.streaming.client import WebSocketClient
from band.core.exceptions import AgentDisconnectedError
from band.testing import fake_phoenix_server
from tests.agent.wirekit import (
    SUPERSEDE,
    WireTermination,
    completed_wire_agent,
    wire_agent,
)
from tests.platform.test_link_real_wire import host_signal_handler
from tests.runtime.conftest import wait_for_condition


class TestShutdownStatus:
    @pytest.mark.parametrize("hosting", ["agent", "runtime"])
    async def test_default_signal_retains_cleanup_but_not_healthy_run_status(
        self, monkeypatch: pytest.MonkeyPatch, hosting: str
    ) -> None:
        with host_signal_handler(signal.SIGTERM) as host_handler:
            async with fake_phoenix_server() as server:
                agent = wire_agent(server.url, monkeypatch)
                owner = agent if hosting == "agent" else agent.runtime
                assert not owner.status().running
                if hosting == "agent":
                    await agent.start()
                else:
                    await agent.runtime.start(on_execute=AsyncMock())
                started = owner.status()
                running = asyncio.create_task(owner.run_forever())
                try:
                    await asyncio.sleep(0)
                    signal.raise_signal(signal.SIGTERM)
                    await asyncio.wait_for(running, timeout=2.0)
                    assert signal.getsignal(signal.SIGTERM) is host_handler
                    stopped_transport = owner.status()
                    assert not stopped_transport.connected
                    assert not stopped_transport.running
                    assert stopped_transport.cleanup_pending
                    assert stopped_transport.started_at is None
                    assert stopped_transport.last_disconnect_reason is None
                    assert not agent.runtime.status().running
                    await owner.stop()
                    assert not owner.status().cleanup_pending
                    if hosting == "agent":
                        await agent.start()
                    else:
                        await agent.runtime.start(on_execute=AsyncMock())
                    restarted = owner.status()
                    assert restarted.running and restarted.connected
                    assert not restarted.cleanup_pending
                    assert started.started_at is not None
                    assert restarted.started_at is not None
                    assert restarted.started_at > started.started_at
                finally:
                    await owner.stop()
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)

    @pytest.mark.parametrize("hosting", ["agent", "runtime"])
    async def test_cancelled_waiter_keeps_the_current_run_and_timestamp(
        self, monkeypatch: pytest.MonkeyPatch, hosting: str
    ) -> None:
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch)
            owner = agent if hosting == "agent" else agent.runtime
            assert not owner.status().running
            if hosting == "agent":
                await agent.start()
            else:
                await agent.runtime.start(on_execute=AsyncMock())
            started = owner.status()
            running = asyncio.create_task(owner.run_forever())
            replacement = None
            try:
                await asyncio.sleep(0)
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running
                live = owner.status()
                assert live.running and live.connected and not live.cleanup_pending
                assert live.started_at == started.started_at
                await agent.runtime.link.subscribe_room("still-live")
                assert "chat_room:still-live" in server.joined_topics
                replacement = asyncio.create_task(
                    owner.run_forever(install_signal_handlers=False)
                )
                await server.push("agent_control:agent-123", "supersede", SUPERSEDE)
                with pytest.raises(AgentDisconnectedError) as raised:
                    await asyncio.wait_for(replacement, timeout=2.0)
                assert raised.value.reason is owner.last_disconnect_reason
            finally:
                await owner.stop()
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
                if replacement is not None:
                    replacement.cancel()
                    await asyncio.gather(replacement, return_exceptions=True)

    @pytest.mark.parametrize("hosting", ["agent", "runtime"])
    async def test_cancel_at_shutdown_keeps_completed_transport_status(
        self, monkeypatch: pytest.MonkeyPatch, hosting: str
    ) -> None:
        with host_signal_handler(signal.SIGTERM):
            async with fake_phoenix_server() as server:
                agent = wire_agent(server.url, monkeypatch)
                owner = agent if hosting == "agent" else agent.runtime
                if hosting == "agent":
                    await agent.start()
                else:
                    await agent.runtime.start(on_execute=AsyncMock())
                assert owner.status().started_at is not None
                running = asyncio.create_task(owner.run_forever())

                async def cancel_at_shutdown() -> bool:
                    await wait_for_condition(
                        lambda: not agent.runtime.link.is_connected,
                        timeout=2.0,
                        interval=0,
                    )
                    return running.cancel()

                observer = asyncio.create_task(cancel_at_shutdown())
                try:
                    await asyncio.sleep(0)
                    signal.raise_signal(signal.SIGTERM)
                    assert await asyncio.wait_for(observer, timeout=2.0)
                    with pytest.raises(asyncio.CancelledError):
                        await running
                    stopped = owner.status()
                    assert not stopped.running
                    assert not stopped.connected
                    assert stopped.cleanup_pending
                    assert stopped.started_at is None
                    await owner.stop()
                    assert not owner.status().cleanup_pending
                    if hosting == "agent":
                        await agent.start()
                    else:
                        await agent.runtime.start(on_execute=AsyncMock())
                    restarted = owner.status()
                    assert restarted.running and restarted.connected
                    assert restarted.started_at is not None
                    assert not restarted.cleanup_pending
                finally:
                    await owner.stop()
                    observer.cancel()
                    running.cancel()
                    await asyncio.gather(observer, running, return_exceptions=True)

    @pytest.mark.parametrize("hosting", ["agent", "runtime"])
    async def test_old_transport_waiter_cannot_clear_restarted_status(
        self, monkeypatch: pytest.MonkeyPatch, hosting: str
    ) -> None:
        completed, release = asyncio.Event(), asyncio.Event()
        original = WebSocketClient.run_forever
        first = True

        async def delayed_first_return(
            client: WebSocketClient, *, install_signal_handlers: bool = True
        ) -> None:
            nonlocal first
            delay_this_return = first
            first = False
            await original(client, install_signal_handlers=install_signal_handlers)
            if delay_this_return:
                completed.set()
                await release.wait()

        monkeypatch.setattr(WebSocketClient, "run_forever", delayed_first_return)
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch)
            owner = agent if hosting == "agent" else agent.runtime
            if hosting == "agent":
                await agent.start()
            else:
                await agent.runtime.start(on_execute=AsyncMock())
            first_started_at = owner.status().started_at
            old_waiter = asyncio.create_task(
                owner.run_forever(install_signal_handlers=False)
            )
            try:
                await asyncio.sleep(0)
                await owner.stop()
                await asyncio.wait_for(completed.wait(), timeout=2.0)
                if hosting == "agent":
                    await agent.start()
                else:
                    await agent.runtime.start(on_execute=AsyncMock())
                restarted = owner.status()
                assert restarted.started_at != first_started_at
                release.set()
                await asyncio.wait_for(old_waiter, timeout=2.0)
                assert owner.status() == restarted
                await agent.runtime.link.subscribe_room("restart-status-live")
                assert "chat_room:restart-status-live" in server.joined_topics
                assert server.connection_count == 2
            finally:
                release.set()
                await owner.stop()
                old_waiter.cancel()
                await asyncio.gather(old_waiter, return_exceptions=True)

    @pytest.mark.parametrize("hosting", ["agent", "runtime"])
    @pytest.mark.parametrize("termination", ["signal_cancel", "policy_close"])
    async def test_transport_completion_without_successful_waiter_clears_health(
        self,
        monkeypatch: pytest.MonkeyPatch,
        hosting: str,
        termination: WireTermination,
    ) -> None:
        async with completed_wire_agent(
            monkeypatch, hosting=hosting, termination=termination
        ) as (agent, owner, server):
            for stopped in (agent.status(), agent.runtime.status()):
                assert not stopped.running
                assert not stopped.connected
                assert stopped.cleanup_pending
                assert stopped.started_at is None
            await owner.stop()
            assert not owner.status().cleanup_pending
            if hosting == "agent":
                await agent.start()
            else:
                await agent.runtime.start(on_execute=AsyncMock())
            restarted = owner.status()
            assert restarted.running and restarted.connected
            assert restarted.started_at is not None
            assert not restarted.cleanup_pending
            await agent.runtime.link.subscribe_room("completed-status-restart")
            assert "chat_room:completed-status-restart" in server.joined_topics
            assert server.connection_count == 2

    @pytest.mark.parametrize("hosting", ["agent", "runtime"])
    async def test_startup_cannot_restore_health_after_transport_completion(
        self, monkeypatch: pytest.MonkeyPatch, hosting: str
    ) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch, room_ids=())
            await agent.runtime.initialize()
            link = agent.runtime.link
            list_chats = link.rest.agent_api_chats.list_agent_chats

            async def delayed_listing(*args: object, **kwargs: object) -> object:
                entered.set()
                await release.wait()
                return await list_chats(*args, **kwargs)

            monkeypatch.setattr(
                link.rest.agent_api_chats, "list_agent_chats", delayed_listing
            )
            owner = agent if hosting == "agent" else agent.runtime

            async def start() -> None:
                if hosting == "agent":
                    await agent.start()
                else:
                    await agent.runtime.start(on_execute=AsyncMock())

            starting = asyncio.create_task(start())
            try:
                await asyncio.wait_for(entered.wait(), timeout=2.0)
                assert link.is_connected
                ws = link._ws
                assert ws is not None and ws.client is not None
                supervisor = ws.client._supervisor_task
                assert supervisor is not None
                await server.close_connection(code=1008, reason="policy violation")
                await asyncio.wait_for(asyncio.shield(supervisor), timeout=2.0)
                assert not link.is_connected
                release.set()
                await asyncio.wait_for(starting, timeout=2.0)
                stopped = owner.status()
                assert not stopped.running and not stopped.connected
                assert stopped.started_at is None and stopped.cleanup_pending
                await owner.stop()
                assert not owner.status().cleanup_pending
                await start()
                restarted = owner.status()
                assert restarted.running and restarted.connected
                assert restarted.started_at is not None
                assert not restarted.cleanup_pending
                assert server.connection_count == 2
            finally:
                release.set()
                await asyncio.gather(starting, return_exceptions=True)
                await owner.stop()
