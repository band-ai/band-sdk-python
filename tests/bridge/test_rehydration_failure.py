"""A rehydration nudge that fails while the bridge starts up."""

from __future__ import annotations

import dataclasses
import logging

import pytest

from tests.bridge.kit import (
    MultiRoomHangingForwarder,
    make_platform_message,
    rehydrating_runner,
)

FAILING_ROOM = "r1"
OTHER_ROOM = "r2"


class TestFailedRehydrationNudge:
    async def test_a_failed_nudge_is_logged_and_the_other_rooms_still_rehydrate(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        forwarder = MultiRoomHangingForwarder([])
        # A null-content /next message cannot be wrapped as a message event.
        unwrappable = dataclasses.replace(
            make_platform_message("m-failing", FAILING_ROOM), content=None
        )
        runner, link = rehydrating_runner(
            forwarder, unwrappable, make_platform_message("m-other", OTHER_ROOM)
        )

        with caplog.at_level(logging.INFO, logger="bridge_core.bridge"):
            await runner._connect_and_consume()

        assert [p["room_id"] for p in forwarder.forwarded] == [OTHER_ROOM]
        link.__anext__.assert_awaited()
        assert [
            record.getMessage()
            for record in caplog.records
            if record.levelno == logging.ERROR
        ] == [f"Agent agent-1: rehydration nudge for room {FAILING_ROOM} failed"]
