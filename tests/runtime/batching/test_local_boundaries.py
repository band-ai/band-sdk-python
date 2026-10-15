"""Known local segment membership wins over message timestamp and UUID order."""

from __future__ import annotations

import pytest

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
