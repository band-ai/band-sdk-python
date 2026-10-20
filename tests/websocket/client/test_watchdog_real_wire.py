"""The heartbeat dead-threshold watchdog over a real in-process WebSocket peer."""

from __future__ import annotations

import asyncio

from band.client.streaming import WebSocketClient
from tests.websocket.client.kit import phoenix_peer
from tests.websocket.conftest import fast_session_policy


class TestWatchdogOverRealWire:
    async def test_watchdog_ack_keeps_connection_alive_across_heartbeat_cycles(self):
        """A real heartbeat/ack round-trip resets the watchdog deadline each
        cycle, so the connection survives well past a single dead_threshold_s
        window without the watchdog ever tripping."""
        policy = fast_session_policy(heartbeat_interval_s=0.15, dead_threshold_s=0.4)
        async with (
            phoenix_peer() as (ws_url, connected),
            WebSocketClient(
                ws_url, "test-key", "agent-123", session_policy=policy
            ) as client,
        ):
            await asyncio.wait_for(connected, timeout=5)
            await asyncio.sleep(1.0)
            assert client.client is not None
            assert client.client.connection is not None
            assert client.client.connection.close_code is None

    async def test_watchdog_forces_close_and_reconnect_when_ack_withheld(self):
        """A withheld ack forces close_connection at (approximately)
        dead_threshold_s, and the forced close flows into the client's own
        reconnect path -- on_reconnect fires once the new connection lands."""
        policy = fast_session_policy(heartbeat_interval_s=0.15, dead_threshold_s=0.4)
        reconnected = asyncio.Event()

        async def on_reconnect() -> None:
            reconnected.set()

        async with (
            phoenix_peer(ack_heartbeats=False) as (ws_url, connected),
            WebSocketClient(
                ws_url,
                "test-key",
                "agent-123",
                on_reconnect=on_reconnect,
                session_policy=policy,
            ),
        ):
            await asyncio.wait_for(connected, timeout=5)
            start = asyncio.get_running_loop().time()
            await asyncio.wait_for(reconnected.wait(), timeout=5)
            elapsed = asyncio.get_running_loop().time() - start

        assert elapsed >= policy.dead_threshold_s * 0.5

    async def test_watchdog_task_cancelled_cleanly_on_aexit(self):
        """__aexit__ stops the watchdog -- no dangling task survives shutdown.

        (HeartbeatWatchdog's own cancellation behavior is covered directly in
        test_watchdog.py; this test only checks WebSocketClient wires __aexit__
        to it.)"""
        policy = fast_session_policy(heartbeat_interval_s=0.05, dead_threshold_s=5.0)
        async with phoenix_peer() as (ws_url, connected):
            client = WebSocketClient(
                ws_url, "test-key", "agent-123", session_policy=policy
            )
            async with client:
                await asyncio.wait_for(connected, timeout=5)
                watchdog_task = client._watchdog._task
                assert watchdog_task is not None
                assert not watchdog_task.done()

        assert watchdog_task.done()
        assert watchdog_task.cancelled()
        assert client._watchdog._task is None
