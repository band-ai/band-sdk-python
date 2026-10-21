"""BandLink owns cleanup when cancellation interrupts startup reconciliation."""

from __future__ import annotations

import asyncio

import pytest

from band.testing import fake_phoenix_server
from tests.platform.linkclose.kit import CONTROL_TOPIC, connected, make_link
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.heartbeat import ControlProbe


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestStartupCleanup:
    async def test_cancelled_control_join_closes_socket_and_allows_reuse(self) -> None:
        async with controlled_clock() as clock:
            before = asyncio.all_tasks()
            async with fake_phoenix_server() as server:
                link = make_link(server.url)
                probe = ControlProbe()
                link.on_control = probe.receive
                with server.defer_join(CONTROL_TOPIC) as deferred:
                    async with background(link.connect()) as starting:
                        await deferred.ready.wait()
                        first = await server.wait_for_connection(1)
                        await clock.elapse(5)
                        starting.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await starting
                        await first.socket.wait_closed()
                    assert not link.is_connected
                async with connected(link):
                    second = await server.wait_for_connection(2)
                    await second.wait_for_join(CONTROL_TOPIC)
                    await clock.elapse(30)
                    await second.wait_for_heartbeats(1)
                    await probe.roundtrip(server)
                await second.socket.wait_closed()
            assert asyncio.all_tasks() == before
