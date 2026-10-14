"""An entrance that finds an original owed a terminal write settles it itself."""

from __future__ import annotations

from itertools import product

import pytest

from tests.runtime.recovery.kit import (
    TURNS,
    Ending,
    Entrance,
    Fault,
    Harness,
)

pytestmark = pytest.mark.looptime


def finds_the_original(entrance: Entrance, fault: Fault) -> bool:
    """Whether ``entrance`` can see an original whose terminal write is owed.

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
    (shape, ending, entrance, fault)
    for (shape, ending), entrance, fault in product(TURNS, Entrance, Fault)
    if finds_the_original(entrance, fault)
]


@pytest.mark.parametrize(("shape", "ending", "entrance", "fault"), REACHING)
class TestEntranceReach:
    async def test_the_entrance_writes_the_owed_mark_once(
        self, harness: Harness, ending: Ending, entrance: Entrance, fault: Fault
    ) -> None:
        await harness.outage_then(fault, entrance)

        assert harness.model_turns == [harness.members]
        assert harness.recovery == {m: [ending.intent] for m in harness.members}
        assert harness.member_statuses == dict.fromkeys(harness.members, ending.intent)
