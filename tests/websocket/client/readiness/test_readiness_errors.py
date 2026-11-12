"""A raising readiness callback is logged and never ends the subscription."""

from __future__ import annotations

import logging

import pytest

from band.client.streaming import RoomReadiness
from band.client.streaming.readiness import logger as readiness_logger
from tests.websocket.client.readiness.kit import (
    ROOM_ID,
    Delivered,
    Timeline,
    crash_room_channel,
    push_message,
    readiness,
    readiness_session,
)


class TestReadinessErrors:
    async def test_a_failing_callback_keeps_delivery_and_later_signals(
        self, timeline: Timeline, caplog: pytest.LogCaptureFixture
    ) -> None:
        def failing_ready(signal: RoomReadiness) -> None:
            timeline.ready(signal)
            raise RuntimeError("consumer bug")

        caplog.set_level(logging.ERROR, logger=readiness_logger.name)
        async with readiness_session(timeline) as (server, client):
            await client.join_chat_room_channel(
                ROOM_ID, timeline.deliver, on_ready=failing_ready
            )
            await push_message(server, "m1")
            await timeline.wait_for(Delivered("m1"))
            await crash_room_channel(server)
            await timeline.wait_for(readiness(2))

            assert timeline.entries == [readiness(1), Delivered("m1"), readiness(2)]
            assert [r.exc_info[0] for r in caplog.records if r.exc_info] == [
                RuntimeError,
                RuntimeError,
            ]
