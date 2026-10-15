"""A pass that parks or drops input logs which messages and why."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from band.runtime.delivery import DeliveryLifecycle, TurnOutcome
from tests.runtime.batching.kit import FakeRoom, started_room
from tests.runtime.helpers import ROOM_ID

pytestmark = pytest.mark.looptime

EXECUTION_LOGGER = "band.runtime.execution"
SCHEDULER_LOGGER = "band.runtime.scheduler"


@pytest.fixture(autouse=True)
def capture_execution_log(caplog: pytest.LogCaptureFixture) -> None:
    for name in (EXECUTION_LOGGER, SCHEDULER_LOGGER):
        caplog.set_level(logging.DEBUG, logger=name)


def room_debug(text: str, *, owner: str = "Room") -> tuple[int, str]:
    """The (level, message) the room's execution logs for ``text`` at DEBUG."""
    return logging.DEBUG, f"{owner} {ROOM_ID}: {text}"


def execution_lines(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [
        (record.levelno, record.getMessage())
        for record in caplog.records
        if record.name in (EXECUTION_LOGGER, SCHEDULER_LOGGER)
    ]


class TestParkingLog:
    async def test_a_repeated_selection_names_the_messages_it_stops_on(
        self, platform: FakeRoom, caplog: pytest.LogCaptureFixture
    ) -> None:
        platform.post("m1")
        advanced_without_effect = AsyncMock(return_value=TurnOutcome.ADVANCED)

        with patch.object(DeliveryLifecycle, "run", advanced_without_effect):
            async with started_room(platform) as room:
                await room.quiesce()

        assert room_debug(
            "Selection ('m1',) repeated without progress; "
            "leaving it for the next resync"
        ) in execution_lines(caplog)

    async def test_a_released_original_is_named(
        self, platform: FakeRoom, caplog: pytest.LogCaptureFixture
    ) -> None:
        arrival = platform.post("m1", status="failed")

        async with started_room(platform, arrived=(arrival,)) as room:
            await room.quiesce()

        assert room_debug(
            "Releasing retained message(s) ['m1'] that no turn will run"
        ) in execution_lines(caplog)

    async def test_a_held_message_is_named_as_the_head_it_waits_behind(
        self, platform: FakeRoom, caplog: pytest.LogCaptureFixture
    ) -> None:
        arrival = platform.post("m1")

        async with started_room(platform) as room:
            room.ctx.claims.try_claim(ROOM_ID, "m1")
            await room.deliver(arrival)
            await room.quiesce()

        assert room_debug(
            "Message m1 is held by another claim; deferring behind it"
        ) in execution_lines(caplog)

    async def test_a_refused_input_carries_the_hosts_reason(
        self, platform: FakeRoom, caplog: pytest.LogCaptureFixture
    ) -> None:
        async with started_room(platform) as room:
            room.turns.accepting = False
            await room.deliver(platform.post("m1"))
            await room.quiesce()

        assert room_debug(
            "Input m1 not accepted (busy); keeping it pending for scheduler recovery",
            owner="ExecutionContext",
        ) in execution_lines(caplog)
