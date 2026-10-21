"""Core policy delegated to the released Phoenix transport over real sockets."""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import WebSocketClient
from band.testing import fake_phoenix_server
from tests.platform.linkclose.kit import AGENT_ID, CONTROL_TOPIC
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.heartbeat import ControlProbe, socket_of
from tests.websocket.conftest import fast_session_policy


@pytest.mark.looptime(resolution=binary_clock_resolution())
@pytest.mark.usefixtures("millisecond_reconnects")
class TestHeartbeatPolicy:
    async def test_nondefault_interval_and_healthy_renewal(self) -> None:
        policy = fast_session_policy(heartbeat_interval_s=4, dead_threshold_s=11)
        probe = ControlProbe()
        async with (
            controlled_clock() as clock,
            fake_phoenix_server() as server,
            WebSocketClient(
                server.url, "test-key", AGENT_ID, session_policy=policy
            ) as client,
        ):
            await client.join_agent_control_channel(
                AGENT_ID,
                on_supersede=probe.unexpected_supersede,
                on_control=probe.receive,
            )
            observed = await server.wait_for_connection(1)
            await clock.elapse(3)
            await probe.roundtrip(server)
            assert observed.heartbeats == []
            await clock.elapse(1)
            await observed.wait_for_heartbeats(1)
            await probe.roundtrip(server)
            for count in range(2, 10):
                await clock.elapse(4)
                await observed.wait_for_heartbeats(count)
                await probe.roundtrip(server)
            assert socket_of(client).close_code is None
            assert server.connection_count == 1

    async def test_silence_closes_old_socket_despite_pong_and_control_traffic(
        self,
    ) -> None:
        policy = fast_session_policy(heartbeat_interval_s=4, dead_threshold_s=11)
        disconnected = asyncio.Event()
        reconnected = asyncio.Event()
        ordering: list[str] = []
        probe = ControlProbe()

        async def on_disconnect(error: Exception | None) -> None:
            ordering.append("disconnect")
            disconnected.set()

        async def on_reconnect() -> None:
            replacement = await server.wait_for_connection(2)
            assert CONTROL_TOPIC in replacement.joined_topics
            ordering.append("reconnect")
            reconnected.set()

        async with (
            controlled_clock() as clock,
            fake_phoenix_server(reply_heartbeats=False) as server,
            WebSocketClient(
                server.url,
                "test-key",
                AGENT_ID,
                session_policy=policy,
                on_reconnect=on_reconnect,
                on_disconnect=on_disconnect,
            ) as client,
        ):
            await client.join_agent_control_channel(
                AGENT_ID,
                on_supersede=probe.unexpected_supersede,
                on_control=probe.receive,
            )
            old = await server.wait_for_connection(1)
            old_socket = socket_of(client)
            await clock.elapse(4)
            await old.wait_for_heartbeats(1)
            pong = await old_socket.ping()
            await pong
            await probe.roundtrip(server)
            await clock.elapse(6)
            await probe.roundtrip(server)
            assert old_socket.close_code is None
            assert len(old.heartbeats) == 1
            await clock.elapse(1)
            await old.socket.wait_closed()
            await disconnected.wait()
            await clock.elapse(0.01)
            replacement = await server.wait_for_connection(2)
            await replacement.wait_for_join(CONTROL_TOPIC)
            await reconnected.wait()
            assert ordering == ["disconnect", "reconnect"]
            assert old_socket.close_code is not None
            await clock.elapse(10)
            await replacement.wait_for_heartbeats(1)
            await probe.roundtrip(server)
            assert socket_of(client).close_code is None
            await clock.elapse(1)
            await replacement.socket.wait_closed()
