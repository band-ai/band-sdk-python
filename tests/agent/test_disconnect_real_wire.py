"""Terminal disconnect classification through public Agent lifecycle methods."""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock

import pytest
from websockets.exceptions import ConnectionClosed

import band
from band.client.streaming.client import WebSocketClient
from band.core.exceptions import BandConnectionError
from band.testing import fake_phoenix_server
from tests.agent.kit import MinimalAdapter
from tests.agent.wirekit import (
    SUPERSEDE,
    WireTermination,
    completed_wire_agent,
    wire_agent,
)
from tests.platform.test_link_real_wire import host_signal_handler
from tests.runtime.conftest import wait_for_condition


class TestAgentDisconnect:
    @pytest.mark.parametrize("install_signal_handlers", [False, True])
    async def test_terminal_event_raises_without_waiting_for_socket_close(
        self, monkeypatch: pytest.MonkeyPatch, install_signal_handlers: bool
    ) -> None:
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch)
            await agent.start()
            running = asyncio.create_task(
                agent.run_forever(install_signal_handlers=install_signal_handlers)
            )
            try:
                await server.push("agent_control:agent-123", "supersede", SUPERSEDE)
                with pytest.raises(BandConnectionError) as raised:
                    await asyncio.wait_for(running, timeout=2.0)
                assert isinstance(raised.value, band.AgentDisconnectedError)
                assert raised.value.reason is agent.last_disconnect_reason
                assert agent.runtime.last_disconnect_reason is raised.value.reason
                assert raised.value.reason.reason == "session.already_connected"
                assert raised.value.reason.correlation_id == "evict-1"
                assert not agent.runtime.link.is_connected
                assert server.connection_count == 1
                assert "agent_control:agent-123" in server.joined_topics
            finally:
                await agent.stop()
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
            assert agent.last_disconnect_reason is raised.value.reason

    @pytest.mark.parametrize("install_signal_handlers", [False, True])
    async def test_public_stop_intent_wins_while_room_cleanup_is_waiting(
        self, monkeypatch: pytest.MonkeyPatch, install_signal_handlers: bool
    ) -> None:
        cleanup_entered, release_cleanup = asyncio.Event(), asyncio.Event()

        async def cleanup_room(room_id: str) -> None:
            assert room_id == "room-1"
            cleanup_entered.set()
            await release_cleanup.wait()

        adapter = MinimalAdapter()
        adapter.on_cleanup = AsyncMock(side_effect=cleanup_room)
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch, adapter=adapter)
            await agent.start()
            await wait_for_condition(
                lambda: "room-1" in agent.runtime.runtime.executions
            )
            running = asyncio.create_task(
                agent.run_forever(install_signal_handlers=install_signal_handlers)
            )
            await asyncio.sleep(0)
            stopping = asyncio.create_task(agent.stop())
            try:
                await asyncio.wait_for(cleanup_entered.wait(), timeout=2.0)
                await server.push("agent_control:agent-123", "supersede", SUPERSEDE)
                await asyncio.wait_for(running, timeout=2.0)
                assert not stopping.done()
                assert agent.last_disconnect_reason is not None
                assert (
                    agent.last_disconnect_reason.reason == "session.already_connected"
                )
            finally:
                release_cleanup.set()
                await stopping
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)

    async def test_restart_clears_terminal_reason_and_host_stop_intent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch)
            await agent.start()
            first_run = asyncio.create_task(
                agent.run_forever(install_signal_handlers=False)
            )
            try:
                await server.push("agent_control:agent-123", "supersede", SUPERSEDE)
                with pytest.raises(BandConnectionError):
                    await asyncio.wait_for(first_run, timeout=2.0)
            finally:
                await agent.stop()
                first_run.cancel()
                await asyncio.gather(first_run, return_exceptions=True)
            assert agent.last_disconnect_reason is not None

            await agent.start()
            second_run = asyncio.create_task(
                agent.run_forever(install_signal_handlers=False)
            )
            try:
                assert agent.last_disconnect_reason is None
                await asyncio.sleep(0)
                assert not second_run.done()
                await agent.stop()
                await asyncio.wait_for(second_run, timeout=2.0)
            finally:
                await agent.stop()
                second_run.cancel()
                await asyncio.gather(second_run, return_exceptions=True)

    async def test_default_signal_publishes_completed_transport_shutdown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with host_signal_handler(signal.SIGTERM) as host_handler:
            async with fake_phoenix_server() as server:
                agent = wire_agent(server.url, monkeypatch)
                await agent.start()
                running = asyncio.create_task(agent.run_forever())
                try:
                    await asyncio.sleep(0)
                    signal.raise_signal(signal.SIGTERM)
                    await asyncio.wait_for(running, timeout=2.0)
                    assert signal.getsignal(signal.SIGTERM) is host_handler
                    assert not agent.runtime.link.is_connected
                    assert agent.last_disconnect_reason is None
                    with pytest.raises(ConnectionClosed):
                        await server.push(
                            "agent_control:agent-123", "supersede", SUPERSEDE
                        )
                finally:
                    await agent.stop()
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)

    @pytest.mark.parametrize("install_signal_handlers", [False, True])
    async def test_cancelled_waiter_keeps_live_transport_and_replacement_waiter(
        self, monkeypatch: pytest.MonkeyPatch, install_signal_handlers: bool
    ) -> None:
        with host_signal_handler(signal.SIGTERM) as host_handler:
            async with fake_phoenix_server() as server:
                agent = wire_agent(server.url, monkeypatch)
                await agent.start()
                running = asyncio.create_task(
                    agent.run_forever(install_signal_handlers=install_signal_handlers)
                )
                replacement = None
                try:
                    await asyncio.sleep(0)
                    running.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await running
                    assert signal.getsignal(signal.SIGTERM) is host_handler
                    assert agent.runtime.link.is_connected
                    await agent.runtime.link.subscribe_room("live-after-cancel")
                    assert "chat_room:live-after-cancel" in server.joined_topics
                    assert server.connection_count == 1
                    replacement = asyncio.create_task(
                        agent.run_forever(install_signal_handlers=False)
                    )
                    await server.push("agent_control:agent-123", "supersede", SUPERSEDE)
                    with pytest.raises(BandConnectionError) as raised:
                        await asyncio.wait_for(replacement, timeout=2.0)
                    assert isinstance(raised.value, band.AgentDisconnectedError)
                finally:
                    await agent.stop()
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)
                    if replacement is not None:
                        replacement.cancel()
                        await asyncio.gather(replacement, return_exceptions=True)

    @pytest.mark.parametrize("hosting", ["agent", "runtime"])
    async def test_cancel_after_completed_transport_keeps_runtime_stopped(
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
                    assert not agent.runtime.link.is_connected
                    assert agent.runtime.cleanup_pending
                    assert owner.last_disconnect_reason is None
                    with pytest.raises(ConnectionClosed):
                        await server.push(
                            "agent_control:agent-123", "supersede", SUPERSEDE
                        )
                finally:
                    await owner.stop()
                    observer.cancel()
                    running.cancel()
                    await asyncio.gather(observer, running, return_exceptions=True)

    @pytest.mark.parametrize("hosting", ["agent", "runtime"])
    async def test_old_waiter_completion_does_not_stop_a_restarted_transport(
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
                assert agent.runtime.link.is_connected
                release.set()
                await asyncio.wait_for(old_waiter, timeout=2.0)
                assert agent.runtime.link.is_connected
                assert not agent.runtime.cleanup_pending
                await agent.runtime.link.subscribe_room("restart-still-live")
                assert "chat_room:restart-still-live" in server.joined_topics
                assert server.connection_count == 2
            finally:
                release.set()
                await owner.stop()
                old_waiter.cancel()
                await asyncio.gather(old_waiter, return_exceptions=True)

    @pytest.mark.parametrize("hosting", ["agent", "runtime"])
    async def test_cancel_during_reconnect_does_not_end_the_current_run(
        self, monkeypatch: pytest.MonkeyPatch, hosting: str
    ) -> None:
        disconnected, resume_reconnect = asyncio.Event(), asyncio.Event()
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch)
            await agent.runtime.initialize()
            link = agent.runtime.link
            original = link._on_disconnected

            async def hold_disconnect(error: Exception | None) -> None:
                await original(error)
                disconnected.set()
                await resume_reconnect.wait()

            monkeypatch.setattr(link, "_on_disconnected", hold_disconnect)
            owner = agent if hosting == "agent" else agent.runtime
            if hosting == "agent":
                await agent.start()
            else:
                await agent.runtime.start(on_execute=AsyncMock())
            running = asyncio.create_task(
                owner.run_forever(install_signal_handlers=False)
            )
            try:
                await server.abort_connection()
                await asyncio.wait_for(disconnected.wait(), timeout=2.0)
                assert not link.is_connected
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running
                assert not agent.runtime.cleanup_pending
                assert owner.last_disconnect_reason is None
                resume_reconnect.set()
                await wait_for_condition(lambda: link.is_connected, timeout=10.0)
                await link.subscribe_room("reconnected-still-live")
                assert "chat_room:reconnected-still-live" in server.joined_topics
                assert server.connection_count == 2
            finally:
                resume_reconnect.set()
                await owner.stop()
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)

    @pytest.mark.parametrize("hosting", ["agent", "runtime"])
    @pytest.mark.parametrize(
        "termination", ["signal_cancel", "policy_close", "normal_close"]
    )
    async def test_transport_completion_without_successful_waiter_retains_custody(
        self,
        monkeypatch: pytest.MonkeyPatch,
        hosting: str,
        termination: WireTermination,
    ) -> None:
        async with completed_wire_agent(
            monkeypatch, hosting=hosting, termination=termination
        ) as (agent, owner, server):
            assert not agent.runtime.link.is_connected
            assert agent.runtime.cleanup_pending
            await owner.stop()
            assert not agent.runtime.cleanup_pending
            if hosting == "agent":
                await agent.start()
            else:
                await agent.runtime.start(on_execute=AsyncMock())
            assert agent.runtime.link.is_connected
            await agent.runtime.link.subscribe_room("completed-lifetime-restart")
            assert "chat_room:completed-lifetime-restart" in server.joined_topics
            assert server.connection_count == 2
