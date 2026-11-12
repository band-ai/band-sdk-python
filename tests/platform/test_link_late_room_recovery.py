"""A room that recovers after its reconnect's reconcile still catches up."""

from __future__ import annotations

import pytest

from band.platform.event import ReconnectedEvent
from band.testing import fake_phoenix_server
from tests.platform.linkclose.kit import connected, make_link
from tests.platform.recovery.kit import ROOM_ID, ROOM_TOPIC
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestLinkLateRoomRecovery:
    async def test_a_rejoin_timing_out_in_the_reconnect_catches_up_on_recovery(
        self,
    ) -> None:
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            link = make_link(server.url)
            async with connected(link):
                await link.subscribe_room(ROOM_ID)
                assert link._ws is not None
                join_timeout_s = link._ws.client.join_timeout_s
                with server.defer_join(ROOM_TOPIC) as deferred:
                    await server.abort_connection()
                    await clock.run_io(deferred.ready.wait())
                    replacement = await server.wait_for_connection(2)
                    await clock.elapse(join_timeout_s)
                    assert await clock.run_io(anext(link)) == ReconnectedEvent()

                await clock.run_io(replacement.wait_for_join(ROOM_TOPIC))
                assert await clock.run_io(anext(link)) == ReconnectedEvent(
                    room_id=ROOM_ID
                )
                assert server.connection_count == 2
