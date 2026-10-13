"""A control that lands while the bridge rehydrates its rooms at startup."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest
from bridge_core.bridge import AgentRunner

from tests.bridge.kit import (
    MultiRoomHangingForwarder,
    control_signal,
    make_platform_message,
    rehydrating_runner,
)

HANGING_ROOM = "r1"
OTHER_ROOM = "r2"


def runner_with_a_hanging_room() -> tuple[
    AgentRunner, MultiRoomHangingForwarder, MagicMock
]:
    forwarder = MultiRoomHangingForwarder([HANGING_ROOM])
    runner, link = rehydrating_runner(
        forwarder,
        make_platform_message("m-hanging", HANGING_ROOM),
        make_platform_message("m-other", OTHER_ROOM),
    )
    return runner, forwarder, link


class TestStopDuringRehydration:
    async def test_stop_ends_only_its_rooms_nudge_and_consuming_starts(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        runner, forwarder, link = runner_with_a_hanging_room()
        connecting = asyncio.create_task(runner._connect_and_consume())
        await forwarder.started[HANGING_ROOM].wait()

        with caplog.at_level(logging.INFO, logger="bridge_core.bridge"):
            await link.on_control(
                control_signal("stop", scope="room", room_id=HANGING_ROOM)
            )
            await connecting

        assert [p["room_id"] for p in forwarder.forwarded] == [OTHER_ROOM]
        assert "m-hanging" not in runner._processed_message_ids
        link.__anext__.assert_awaited()
        assert any(
            "stopped by a control" in record.getMessage()
            and record.levelno == logging.INFO
            for record in caplog.records
        )

    async def test_cancelling_the_runner_mid_rehydration_propagates(self) -> None:
        runner, forwarder, _ = runner_with_a_hanging_room()
        connecting = asyncio.create_task(runner._connect_and_consume())
        await forwarder.started[HANGING_ROOM].wait()

        connecting.cancel()

        with pytest.raises(asyncio.CancelledError):
            await connecting
