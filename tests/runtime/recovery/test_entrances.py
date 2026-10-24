"""Every recovery entrance retries only the stored terminal intent, never the model."""

from __future__ import annotations

import pytest

from tests.runtime.recovery.kit import (
    TURNS,
    Ending,
    Entrance,
    Fault,
    Harness,
)

pytestmark = pytest.mark.looptime


@pytest.mark.parametrize(("shape", "ending"), TURNS)
@pytest.mark.parametrize("fault", list(Fault))
@pytest.mark.parametrize("entrance", list(Entrance))
class TestRecoveryEntrances:
    async def test_the_owed_write_is_all_the_entrance_sends(
        self, harness: Harness, ending: Ending, fault: Fault, entrance: Entrance
    ) -> None:
        await harness.outage_then(fault, entrance)

        assert harness.model_turns == [harness.members]
        assert harness.recovered_kinds <= {ending.intent}
        assert (harness.peer.reopened, harness.peer.rejected) == ([], [])

    async def test_reconciliation_then_settles_each_original_once(
        self, harness: Harness, ending: Ending, fault: Fault, entrance: Entrance
    ) -> None:
        await harness.outage_then(fault, entrance)

        await harness.recover(Entrance.RECONCILIATION)

        assert harness.model_turns == [harness.members]
        assert harness.recovery == {m: [ending.intent] for m in harness.members}
        assert harness.member_statuses == {m: ending.intent for m in harness.members}
