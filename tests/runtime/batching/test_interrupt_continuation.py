"""An INTERRUPT consumes only a running turn; the room keeps serving what is queued."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.batching.kit import FakeRoom, started_room

pytestmark = pytest.mark.looptime

QUEUED = ("m1", "m2", "m3")
ALL_PROCESSED = dict.fromkeys(QUEUED, DeliveryStatus.PROCESSED)


class TestInterruptContinuation:
    @pytest.mark.parametrize(
        ("batching", "max_queued", "first_turn", "turns"),
        [
            pytest.param(
                True, 1, ("m1",), [("m1",), ("m2",), ("m3",)], id="batch-cap-1"
            ),
            pytest.param(
                True, 2, ("m1", "m2"), [("m1", "m2"), ("m3",)], id="batch-cap-2"
            ),
            pytest.param(False, 10, ("m1",), [("m1",), ("m2",), ("m3",)], id="off"),
        ],
    )
    async def test_an_interrupted_turn_does_not_strand_the_work_queued_behind_it(
        self,
        platform: FakeRoom,
        batching: bool,
        max_queued: int,
        first_turn: tuple[str, ...],
        turns: list[tuple[str, ...]],
    ) -> None:
        arrived = tuple(platform.post(message_id) for message_id in QUEUED)

        async with started_room(
            platform,
            batching=batching,
            max_queued=max_queued,
            gate=True,
            arrived=arrived,
        ) as room:
            await room.until(lambda: room.turns.ids == [first_turn])
            room.turns.gate = False
            await room.interrupt()
            await room.quiesce()

        assert (room.turns.ids, platform.statuses()) == (turns, ALL_PROCESSED)

    async def test_an_interrupt_between_turns_during_a_page_read_does_not_abandon_the_arrival(
        self, platform: FakeRoom
    ) -> None:
        async with started_room(platform) as room:
            await room.quiesce()
            platform.read_actions.append(room.interrupt)
            await room.deliver(platform.post("m1"))
            await room.quiesce()

        assert (room.turns.ids, platform.statuses()) == (
            [("m1",)],
            {"m1": DeliveryStatus.PROCESSED},
        )
