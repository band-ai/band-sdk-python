"""An entrance that only touches an original leaves its write to the next pass."""

from __future__ import annotations

import pytest

from tests.runtime.recovery.kit import (
    RESYNC_SECONDS,
    Ending,
    Entrance,
    Fault,
    Harness,
    entrance_cases,
)

pytestmark = pytest.mark.looptime


@pytest.mark.parametrize(
    ("shape", "ending", "entrance", "fault"), entrance_cases(paced=True)
)
class TestPacedEntrance:
    async def test_the_entrance_writes_nothing_within_the_interval(
        self, harness: Harness, ending: Ending, entrance: Entrance, fault: Fault
    ) -> None:
        await harness.outage_then(fault, entrance)

        assert harness.recovery == {}

    async def test_the_next_reconciliation_settles_each_original_once(
        self, harness: Harness, ending: Ending, entrance: Entrance, fault: Fault
    ) -> None:
        await harness.outage_then(fault, entrance)

        await harness.advance(RESYNC_SECONDS)

        assert harness.model_turns == [harness.members]
        assert harness.recovery == {m: [ending.intent] for m in harness.members}
        assert harness.member_statuses == dict.fromkeys(harness.members, ending.intent)
