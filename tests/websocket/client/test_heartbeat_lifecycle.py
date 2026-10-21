"""Public client context lifecycles release native heartbeat resources."""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import WebSocketClient
from band.testing import fake_phoenix_server
from tests.platform.linkclose.kit import AGENT_ID
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.heartbeat import ControlProbe
from tests.websocket.conftest import fast_session_policy


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestHeartbeatLifecycle:
    async def test_sequential_reuse_has_fresh_socket_window_and_no_surviving_tasks(
        self,
    ) -> None:
        async with controlled_clock() as clock:
            before = asyncio.all_tasks()
            async with fake_phoenix_server() as server:
                policy = fast_session_policy(
                    heartbeat_interval_s=4, dead_threshold_s=11
                )
                client = WebSocketClient(
                    server.url, "test-key", AGENT_ID, session_policy=policy
                )
                for number in (1, 2):
                    probe = ControlProbe()
                    async with client:
                        await client.join_agent_control_channel(
                            AGENT_ID,
                            on_supersede=probe.unexpected_supersede,
                            on_control=probe.receive,
                        )
                        observed = await server.wait_for_connection(number)
                        await clock.elapse(10)
                        await observed.wait_for_heartbeats(1)
                        await probe.roundtrip(server)
                        assert observed.socket.close_code is None
                    await observed.socket.wait_closed()
                    await clock.elapse(20)
                    assert server.connection_count == number
            assert asyncio.all_tasks() == before
