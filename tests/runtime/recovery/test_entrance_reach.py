"""An entrance that requests a pass finds an original owed a write and settles it."""

from __future__ import annotations

import pytest

from tests.runtime.recovery.kit import Ending, Entrance, Fault, Harness, entrance_cases

pytestmark = pytest.mark.looptime


@pytest.mark.parametrize(
    ("shape", "ending", "entrance", "fault"), entrance_cases(paced=False)
)
class TestEntranceReach:
    async def test_the_entrance_writes_the_owed_mark_once(
        self, harness: Harness, ending: Ending, entrance: Entrance, fault: Fault
    ) -> None:
        await harness.outage_then(fault, entrance)

        assert harness.model_turns == [harness.members]
        assert harness.recovery == {m: [ending.intent] for m in harness.members}
        assert harness.member_statuses == dict.fromkeys(harness.members, ending.intent)
