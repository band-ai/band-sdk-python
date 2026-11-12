"""A previous generation's callback ends before the next readiness signal."""

from __future__ import annotations

import pytest

from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.readiness.kit import (
    ROOM_TOPIC,
    Cancelled,
    Delivered,
    Started,
    Timeline,
    join_room,
    push_message,
    readiness,
    readiness_session,
)


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestOldGenerationCallbacks:
    async def test_a_callback_finishing_in_the_drain_window_precedes_the_signal(
        self, timeline: Timeline
    ) -> None:
        release = timeline.hold("m1")
        async with (
            controlled_clock() as clock,
            readiness_session(timeline) as (server, client),
        ):
            await join_room(client, timeline)
            await push_message(server, "m1")
            await clock.run_io(timeline.wait_for(Started("m1")))
            await server.abort_connection()
            replacement = await clock.run_io(server.wait_for_connection(2))
            await clock.elapse(client.client.callback_drain_timeout_s / 2)
            assert ROOM_TOPIC not in replacement.joined_topics
            assert readiness(2) not in timeline.entries

            release.set()
            await clock.run_io(timeline.wait_for(readiness(2)))
            assert timeline.entries == [
                readiness(1),
                Started("m1"),
                Delivered("m1"),
                readiness(2),
            ]

    async def test_a_callback_stalled_past_the_drain_window_is_cancelled_first(
        self, timeline: Timeline
    ) -> None:
        timeline.hold("m1")
        async with (
            controlled_clock() as clock,
            readiness_session(timeline) as (server, client),
        ):
            await join_room(client, timeline)
            assert client.client is not None
            await push_message(server, "m1")
            await clock.run_io(timeline.wait_for(Started("m1")))
            await server.abort_connection()
            replacement = await clock.run_io(server.wait_for_connection(2))
            await clock.elapse(client.client.callback_drain_timeout_s)
            await clock.run_io(replacement.wait_for_join(ROOM_TOPIC))
            await clock.run_io(timeline.wait_for(readiness(2)))

            assert timeline.entries == [
                readiness(1),
                Started("m1"),
                Cancelled("m1"),
                readiness(2),
            ]
