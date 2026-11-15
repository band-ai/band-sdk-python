"""Known local segment membership wins over message timestamp and UUID order."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.platform.message_lifecycle import ACTIONABLE_PAGE_SIZE
from tests.runtime.batching.boundaries import (
    AFTER,
    BEFORE,
    LAST_BEFORE,
    SYNTHETIC,
    UNKNOWN,
    WARMUP,
    boundary_room,
    local_boundary,
)


@pytest.mark.looptime
class TestLocalBoundaries:
    @pytest.mark.parametrize(
        "synthetic", [False, True], ids=["participant", "synthetic"]
    )
    @pytest.mark.parametrize("cap", [1, 10], ids=["one", "batch"])
    async def test_next_segment_reobserves_failed_original_on_an_earlier_page(
        self, synthetic: bool, cap: int
    ) -> None:
        async with boundary_room(cap=cap) as room:
            before = room.peer.post_at(BEFORE, 2)
            after = room.peer.post_at(AFTER, 1)
            room.peer.rows[AFTER].status = DeliveryStatus.FAILED
            failed_ids = {AFTER}
            for index in range(ACTIONABLE_PAGE_SIZE - 1):
                failed = room.peer.post(f"failed-{index:03}")
                failed.inserted_at = room.peer.rows[AFTER].inserted_at
                failed.status = DeliveryStatus.FAILED
                failed_ids.add(failed.id)
            await room.deliver(before, local_boundary(synthetic=synthetic), after)
            await room.settle(failed=failed_ids)

        boundary = SYNTHETIC if synthetic else "participant_added"
        assert room.turns.ids == [(WARMUP,), (BEFORE,), (boundary,)]
        assert room.peer.statuses[AFTER] is DeliveryStatus.FAILED
        assert room.peer.reopened == []

    @pytest.mark.parametrize("after_second", [1, 2, 3], ids=["older", "equal", "newer"])
    @pytest.mark.parametrize(
        "synthetic", [False, True], ids=["participant", "synthetic"]
    )
    @pytest.mark.parametrize("cap", [1, 10], ids=["one", "batch"])
    async def test_later_original_stays_after_the_local_boundary(
        self, after_second: int, synthetic: bool, cap: int
    ) -> None:
        async with boundary_room(cap=cap) as room:
            before = room.peer.post_at(BEFORE, 2)
            after = room.peer.post_at(AFTER, after_second)
            await room.deliver(before, local_boundary(synthetic=synthetic), after)
            await room.settle()

        boundary = SYNTHETIC if synthetic else "participant_added"
        assert room.turns.ids == [(WARMUP,), (BEFORE,), (boundary,), (AFTER,)]

    async def test_reversed_keys_do_not_split_the_known_leading_segment(self) -> None:
        async with boundary_room() as room:
            before = room.peer.post_at(BEFORE, 2)
            last = room.peer.post_at(LAST_BEFORE, 2)
            room.peer.post_at(UNKNOWN, 2)
            after = room.peer.post_at(AFTER, 2)
            await room.deliver(before, last, local_boundary(synthetic=False), after)
            await room.settle()

        assert room.turns.ids == [
            (WARMUP,),
            (LAST_BEFORE, BEFORE),
            ("participant_added",),
            (AFTER, UNKNOWN),
        ]
