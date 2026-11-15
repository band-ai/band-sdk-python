"""An owed failed write holds ordinary work back exactly as an owed processed write does."""

from __future__ import annotations

import pytest

from tests.runtime.recovery.kit import (
    FOLLOWER,
    Ending,
    Fault,
    Harness,
    Route,
    Shape,
)

pytestmark = pytest.mark.looptime


@pytest.mark.parametrize("shape", [Shape.SINGLETON, Shape.BATCH])
@pytest.mark.parametrize("ending", [Ending.DECLINES, Ending.RAISES])
@pytest.mark.parametrize("fault", list(Fault))
class TestFailedIntentBackpressure:
    async def test_a_later_input_waits_for_the_owed_write(
        self, harness: Harness, fault: Fault
    ) -> None:
        harness.peer.fail_terminals(fault, *harness.members)
        await harness.start(Route.BACKLOG)

        harness.peer.post(FOLLOWER)
        await harness.push(FOLLOWER)

        assert harness.adapter.turns == [harness.members]
