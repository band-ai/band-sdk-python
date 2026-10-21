"""Deferred local-peer joins preserve automatic heartbeat handling."""

from __future__ import annotations

import asyncio

import pytest
from phoenix_channels_python_client.client import PHXChannelsClient
from phoenix_channels_python_client.phx_messages import PHXMessage

from band.testing import fake_phoenix_server
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestDeferredPhoenixJoin:
    async def test_deferred_join_keeps_reading_and_automatically_replying_to_heartbeats(
        self,
    ) -> None:
        acknowledged = asyncio.Event()
        delivered: asyncio.Queue[PHXMessage] = asyncio.Queue()
        async with (
            controlled_clock() as clock,
            fake_phoenix_server() as server,
            PHXChannelsClient(
                server.url,
                "test-key",
                auto_reconnect=False,
                heartbeat_interval_s=4,
                heartbeat_dead_threshold_s=11,
                on_heartbeat_ack=acknowledged.set,
            ) as client,
        ):
            with server.defer_join("probe:room") as deferred:
                async with background(
                    client.subscribe_to_topic("probe:room", delivered.put)
                ) as joining:
                    await deferred.ready.wait()
                    observed = await server.wait_for_connection(1)
                    assert deferred.connection is observed
                    assert "probe:room" not in observed.joined_topics
                    await clock.elapse(4)
                    await observed.wait_for_heartbeats(1)
                    await acknowledged.wait()
                    assert not joining.done()
                    await server.release_join(deferred)
                    await joining
                    await observed.wait_for_join("probe:room")
                    await server.push("probe:room", "probe", {"marker": "released"})
                    assert (await delivered.get()).payload == {"marker": "released"}
