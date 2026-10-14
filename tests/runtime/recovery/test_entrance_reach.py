"""An entrance that finds an original owed a processed write settles it itself."""

from __future__ import annotations

from itertools import product

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.recovery.kit import (
    Entrance,
    Fault,
    Harness,
    Shape,
)

pytestmark = pytest.mark.looptime


def finds_the_original(entrance: Entrance, fault: Fault) -> bool:
    """Whether ``entrance`` can see an original whose processed write is owed.

    A resync always reads the owed writes from the room's own record. A control
    probe or a duplicate delivery only meets an original the room still holds
    back: a probe through the platform's actionable list, which lists it unless
    the platform already applied the write; a duplicate through the payload the
    room retained from the live push.
    """
    match entrance:
        case Entrance.CONTROL_PROBE:
            return not fault.applied
        case _:
            return True


REACHING = [
    (shape, entrance, fault)
    for shape, entrance, fault in product(Shape, Entrance, Fault)
    if finds_the_original(entrance, fault)
]


@pytest.mark.parametrize(("shape", "entrance", "fault"), REACHING)
class TestEntranceReach:
    async def test_the_entrance_writes_the_owed_processed_mark_once(
        self, harness: Harness, entrance: Entrance, fault: Fault
    ) -> None:
        await harness.outage_then(fault, entrance)

        assert harness.model_turns == [harness.members]
        assert harness.recovery == {
            m: [DeliveryStatus.PROCESSED] for m in harness.members
        }
        assert harness.member_statuses == dict.fromkeys(
            harness.members, DeliveryStatus.PROCESSED
        )
