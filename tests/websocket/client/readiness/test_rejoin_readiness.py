"""Every gap is followed by a readiness signal before resumed messages."""

from __future__ import annotations

import pytest

from tests.websocket.client.readiness.kit import (
    ROOM_ID,
    ROOM_TOPIC,
    Delivered,
    GapSource,
    Timeline,
    answer_join_then_push,
    crash_room_channel,
    drop_socket,
    join_room,
    readiness,
    readiness_session,
)


class TestRejoinReadiness:
    @pytest.mark.parametrize(
        ("gap", "connections"),
        [(crash_room_channel, 1), (drop_socket, 2)],
        ids=["same-socket-crash", "socket-drop"],
    )
    async def test_resumed_room_signals_a_gap_before_its_messages(
        self, timeline: Timeline, gap: GapSource, connections: int
    ) -> None:
        async with readiness_session(timeline) as (server, client):
            await join_room(client, timeline)
            with server.defer_join(ROOM_TOPIC) as deferred:
                await gap(server)
                await answer_join_then_push(server, deferred, "m1")
            await timeline.wait_for(Delivered("m1"))

            assert timeline.entries == [readiness(1), readiness(2), Delivered("m1")]
            assert timeline.entries[1].after_gap
            assert client.is_chat_room_ready(ROOM_ID)
            assert server.connection_count == connections
