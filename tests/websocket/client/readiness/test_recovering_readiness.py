"""A room still recovering after a reconnect signals only once it rejoins."""

from __future__ import annotations

import pytest
from phoenix_channels_python_client import ChannelError

from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.readiness.kit import (
    ROOM_ID,
    ROOM_TOPIC,
    Delivered,
    Timeline,
    join_room,
    push_message,
    readiness,
    readiness_session,
)


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestRecoveringReadiness:
    async def test_same_socket_recovery_signals_after_on_reconnect(
        self, timeline: Timeline
    ) -> None:
        async with (
            controlled_clock() as clock,
            readiness_session(timeline) as (server, client),
        ):
            await join_room(client, timeline)
            assert client.client is not None
            client.client.join_timeout_s = 2
            with server.defer_join(ROOM_TOPIC) as deferred:
                await server.abort_connection()
                await clock.run_io(deferred.ready.wait())
                replacement = await server.wait_for_connection(2)
                await clock.elapse(client.client.join_timeout_s)
                await timeline.socket_reconnected.wait()
                assert not client.is_chat_room_ready(ROOM_ID)
                assert timeline.entries == [readiness(1)]

            await clock.run_io(replacement.wait_for_join(ROOM_TOPIC))
            await clock.run_io(timeline.wait_for(readiness(2)))
            await push_message(server, "m1")
            await clock.run_io(timeline.wait_for(Delivered("m1")))
            assert timeline.entries == [readiness(1), readiness(2), Delivered("m1")]
            assert server.connection_count == 2

    async def test_timed_out_initial_join_ignores_its_late_reply(
        self, timeline: Timeline
    ) -> None:
        other = "room-other"
        async with (
            controlled_clock() as clock,
            readiness_session(timeline) as (server, client),
        ):
            assert client.client is not None
            client.client.join_timeout_s = 2
            with server.defer_join(ROOM_TOPIC) as deferred:
                async with clock.background(join_room(client, timeline)) as joining:
                    await clock.run_io(deferred.ready.wait())
                    await clock.elapse(client.client.join_timeout_s)
                    with pytest.raises(ChannelError):
                        await joining
                await server.release_join(deferred)

            # A later join's reply is read after the late one on this socket.
            await join_room(client, timeline, room_id=other)
            assert timeline.entries == [readiness(1, room_id=other)]
