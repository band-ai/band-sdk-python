"""An outage far longer than the old retry budget loses no intent and never spins."""

from __future__ import annotations

import pytest

from tests.platform.settlement.kit import ROOM_ID
from tests.runtime.recovery.kit import (
    CONTROL,
    RESYNC_SECONDS,
    Ending,
    Entrance,
    Fault,
    Harness,
    Route,
    Shape,
)

pytestmark = pytest.mark.looptime

# The budget this replaced was the room's max_message_retries, 1 with batching on.
OUTAGE_PASSES = 12
PACED_INTERVALS = 5
TURNS = [
    (Shape.OFF, Ending.DECLINES),
    (Shape.SINGLETON, Ending.DECLINES),
    (Shape.SINGLETON, Ending.RAISES),
    (Shape.BATCH, Ending.DECLINES),
    (Shape.BATCH, Ending.RAISES),
]


@pytest.mark.parametrize(("shape", "ending"), TURNS)
@pytest.mark.parametrize("fault", list(Fault))
class TestLongOutage:
    async def test_the_intent_outlives_the_outage_without_another_model_turn(
        self, harness: Harness, ending: Ending, fault: Fault
    ) -> None:
        await harness.outage_then(fault, Entrance.RECONCILIATION, passes=OUTAGE_PASSES)

        assert harness.model_turns == [harness.members]
        assert harness.recovery == {m: [ending.intent] for m in harness.members}
        assert harness.member_statuses == dict.fromkeys(harness.members, ending.intent)

    @pytest.mark.parametrize("route", list(Route))
    async def test_a_blockage_is_retried_once_per_interval_not_in_a_spin(
        self, harness: Harness, route: Route, fault: Fault
    ) -> None:
        harness.peer.fail_terminals(fault)
        await harness.start(route)

        per_interval = []
        for _ in range(PACED_INTERVALS):
            before = len(harness.peer.writes)
            await harness.advance(RESYNC_SECONDS / 2)
            assert len(harness.peer.writes) == before
            await harness.advance(RESYNC_SECONDS / 2)
            per_interval.append(len(harness.peer.writes) - before)

        assert len(set(per_interval)) == 1
        assert per_interval[0] >= len(harness.members) * fault.requests_per_write

    async def test_control_discovery_keeps_working_while_writes_are_owed(
        self, harness: Harness, fault: Fault
    ) -> None:
        harness.peer.fail_terminals(fault, *harness.members)
        await harness.start(Route.BACKLOG)
        await harness.advance(OUTAGE_PASSES * RESYNC_SECONDS)

        harness.peer.post(CONTROL)
        await harness.push(CONTROL)

        assert harness.adapter.controls == [CONTROL]
        assert harness.model_turns == [harness.members]
        assert harness.ctx.claims.pending_ack_ids(ROOM_ID) == list(harness.members)
