"""Public Agent lifecycle helpers with a real loopback WebSocket transport."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import pytest
from phoenix_channels_python_client import PHXChannelsClient
from phoenix_channels_python_client.exceptions import PHXConnectionError
from websockets.exceptions import ConnectionClosed

from band.agent import Agent
from band.client.streaming.client import WebSocketClient
from band.core.simple_adapter import SimpleAdapter
from band.platform.link import BandLink
from band.runtime.agentconfig import AgentConfig
from band.runtime.platform_runtime import PlatformRuntime
from band.testing import FakePhoenixServer, fake_phoenix_server
from tests.agent.kit import MinimalAdapter
from tests.platform.test_link_real_wire import host_signal_handler
from tests.runtime.conftest import chat_row, make_link_mock

SUPERSEDE = {
    "reason": "session.already_connected",
    "message": "Superseded by a newer session for this agent.",
    "retryable": False,
    "target_socket_id": "agent_socket:agent-123",
    "correlation_id": "evict-1",
}


def wire_agent(
    server_url: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    adapter: SimpleAdapter | None = None,
    room_ids: tuple[str, ...] = ("room-1",),
) -> Agent:
    """Stub REST only; retain real startup, rooms, stop, and wire callbacks."""
    link = BandLink(
        agent_id="agent-123",
        api_key="test-key",
        ws_url=server_url,
        rest_url="https://test.invalid",
    )
    rest_boundary = make_link_mock()
    link.rest = rest_boundary.rest
    link.rest.agent_api_chats.list_agent_chats = AsyncMock(
        return_value=MagicMock(
            data=[chat_row(room_id) for room_id in room_ids],
            metadata=MagicMock(total_pages=1),
        )
    )
    link.get_next_message = rest_boundary.get_next_message
    link.get_actionable_message = rest_boundary.get_actionable_message
    link.get_stale_processing_messages = AsyncMock(return_value=[])
    link.mark_processing = rest_boundary.mark_processing
    link.mark_processed = rest_boundary.mark_processed
    link.mark_failed = rest_boundary.mark_failed
    link.report_activity = rest_boundary.report_activity
    monkeypatch.setattr("band.runtime.platform_runtime.BandLink", lambda **kwargs: link)
    return Agent.create(
        adapter=adapter or MinimalAdapter(),
        agent_id="agent-123",
        api_key="test-key",
        ws_url=server_url,
        rest_url="https://test.invalid",
        config=AgentConfig(single_instance=False),
    )


WireTermination = Literal["signal_cancel", "policy_close"]


@asynccontextmanager
async def completed_wire_agent(
    monkeypatch: pytest.MonkeyPatch, *, hosting: str, termination: WireTermination
) -> AsyncIterator[tuple[Agent, Agent | PlatformRuntime, FakePhoenixServer]]:
    """Finish the real transport without a successful public run waiter."""
    clients: list[PHXChannelsClient] = []
    entered, release = asyncio.Event(), asyncio.Event()
    original_enter = WebSocketClient.__aenter__
    original_unsubscribe = PHXChannelsClient.unsubscribe_from_topic

    async def capture_enter(client: WebSocketClient) -> WebSocketClient:
        result = await original_enter(client)
        assert client.client is not None
        clients.append(client.client)
        return result

    async def gate_shutdown_unsubscribe(
        client: PHXChannelsClient, topic: str, *, _allow_disconnected: bool = False
    ) -> None:
        if _allow_disconnected:
            entered.set()
            await release.wait()
        await original_unsubscribe(
            client, topic, _allow_disconnected=_allow_disconnected
        )

    monkeypatch.setattr(WebSocketClient, "__aenter__", capture_enter)
    if termination == "signal_cancel":
        monkeypatch.setattr(
            PHXChannelsClient, "unsubscribe_from_topic", gate_shutdown_unsubscribe
        )

    with host_signal_handler(signal.SIGTERM):
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch)
            owner = agent if hosting == "agent" else agent.runtime
            running: asyncio.Task[None] | None = None
            try:
                if hosting == "agent":
                    await agent.start()
                else:
                    await agent.runtime.start(on_execute=AsyncMock())
                assert agent.runtime.link.is_connected
                assert not agent.runtime.cleanup_pending
                client = clients[-1]
                # Observe existing dependency-owned tasks without adding a
                # public waiter or initiating a second shutdown.
                supervisor = client._supervisor_task
                assert supervisor is not None
                match termination:
                    case "signal_cancel":
                        running = asyncio.create_task(owner.run_forever())
                        await asyncio.sleep(0)
                        signal.raise_signal(signal.SIGTERM)
                        await asyncio.wait_for(entered.wait(), timeout=2.0)
                        shutdown = client._shutdown_task
                        assert shutdown is not None and not shutdown.done()
                        running.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await running
                        assert not shutdown.done()
                        release.set()
                        await asyncio.wait_for(asyncio.shield(shutdown), timeout=2.0)
                    case "policy_close":
                        running = asyncio.create_task(
                            owner.run_forever(install_signal_handlers=False)
                        )
                        await asyncio.sleep(0)
                        await server.close_connection(
                            code=1008, reason="policy violation"
                        )
                        with pytest.raises(PHXConnectionError):
                            await asyncio.wait_for(running, timeout=2.0)
                        await asyncio.wait_for(asyncio.shield(supervisor), timeout=2.0)
                with pytest.raises(ConnectionClosed):
                    await server.push("agent_control:agent-123", "supersede", SUPERSEDE)
                yield agent, owner, server
            finally:
                release.set()
                if running is not None:
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)
                await owner.stop()
