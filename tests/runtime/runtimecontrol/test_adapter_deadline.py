"""An adapter hook that never returns is abandoned, so the rooms and signals
behind it are still controlled."""

from __future__ import annotations

import asyncio
import logging

import pytest

from band.client.streaming import ControlMode
from band.core.turn_lifecycle import BACKEND_CANCEL_TIMEOUT_SECONDS
from band.platform.link import BandLink
from tests.runtime.runtimecontrol.kit import ControlledRooms, StoppingAdapter

HANG_GUARD_S = 10 * BACKEND_CANCEL_TIMEOUT_SECONDS


class TestAdapterDeadline:
    @pytest.mark.looptime
    async def test_a_hook_that_never_returns_is_abandoned_for_the_next_room(
        self,
        link: BandLink,
        stopping_adapter: StoppingAdapter,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        rooms = ControlledRooms(
            link, "r1", "r2", on_control=stopping_adapter.on_interrupt
        )

        with caplog.at_level(logging.WARNING, logger="band.runtime.control"):
            await asyncio.wait_for(rooms.signal("stop"), HANG_GUARD_S)

        assert stopping_adapter.heard == [
            ("r1", ControlMode.STOP),
            ("r2", ControlMode.STOP),
        ]
        assert [r.getMessage() for r in caplog.records] == [
            (
                "Adapter stop for room r1 did not finish within "
                f"{BACKEND_CANCEL_TIMEOUT_SECONDS}s; abandoned"
            )
        ]
