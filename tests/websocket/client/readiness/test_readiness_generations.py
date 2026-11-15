"""Generations count confirmed joins of one subscription only."""

from __future__ import annotations

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


class TestReadinessGenerations:
    async def test_a_second_gap_during_recovery_signals_once(
        self, timeline: Timeline
    ) -> None:
        async with readiness_session(timeline) as (server, client):
            await join_room(client, timeline)
            with server.defer_join(ROOM_TOPIC) as deferred:
                await server.abort_connection()
                await deferred.ready.wait()
            # The second socket's join is never answered.
            await server.abort_connection()
            third = await server.wait_for_connection(3)
            await third.wait_for_join(ROOM_TOPIC)
            await timeline.wait_for(readiness(2))
            await push_message(server, "m1")
            await timeline.wait_for(Delivered("m1"))

            assert timeline.entries == [readiness(1), readiness(2), Delivered("m1")]

    async def test_a_replacement_subscription_starts_its_own_generations(
        self, timeline: Timeline
    ) -> None:
        replacement = Timeline()
        async with readiness_session(timeline) as (server, client):
            await join_room(client, timeline)
            await client.leave_chat_room_channel(ROOM_ID)
            await join_room(client, replacement)
            await push_message(server, "m1")
            await replacement.wait_for(Delivered("m1"))

            assert timeline.entries == [readiness(1)]
            assert replacement.entries == [readiness(1), Delivered("m1")]
