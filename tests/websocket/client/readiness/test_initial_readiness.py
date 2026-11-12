"""The initial join signals readiness before its messages and its return."""

from __future__ import annotations

import pytest
from phoenix_channels_python_client import ChannelError

from band.testing import JoinOutcome
from tests.toolkit.tasks import background
from tests.websocket.client.readiness.kit import (
    ROOM_ID,
    ROOM_TOPIC,
    Delivered,
    Timeline,
    answer_join_then_push,
    join_room,
    readiness,
    readiness_session,
)


class TestInitialReadiness:
    async def test_signal_precedes_a_message_right_behind_the_join_reply(
        self, timeline: Timeline
    ) -> None:
        async with readiness_session(timeline) as (server, client):
            with server.defer_join(ROOM_TOPIC) as deferred:
                async with background(join_room(client, timeline)) as joining:
                    await answer_join_then_push(server, deferred, "m1")
                    await joining
            assert readiness(1) in timeline.entries
            assert client.is_chat_room_ready(ROOM_ID)

            await timeline.wait_for(Delivered("m1"))
            assert timeline.entries == [readiness(1), Delivered("m1")]

    async def test_rejected_join_never_signals(self, timeline: Timeline) -> None:
        async with readiness_session(
            timeline, join_outcomes={ROOM_TOPIC: [JoinOutcome.REJECTED]}
        ) as (_, client):
            with pytest.raises(ChannelError):
                await join_room(client, timeline)
            assert timeline.entries == []
            assert not client.is_chat_room_ready(ROOM_ID)
