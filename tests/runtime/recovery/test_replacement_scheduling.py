"""Retained terminal writes keep controls runnable and ordinary work deferred."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.platform.settlement.kit import ROOM_ID
from tests.runtime.recovery.kit import (
    CONTROL,
    FOLLOWER,
    RESYNC_SECONDS,
    TURNS,
    Ending,
    Entrance,
    Fault,
    Harness,
    Route,
)

pytestmark = pytest.mark.looptime


@pytest.mark.parametrize(("shape", "ending"), TURNS)
@pytest.mark.parametrize("fault", list(Fault))
@pytest.mark.parametrize("replace", [False, True], ids=["original", "replacement"])
class TestReplacementScheduling:
    async def test_rest_controls_remain_discoverable_during_an_outage(
        self, harness: Harness, fault: Fault, replace: bool
    ) -> None:
        harness.peer.fail_terminals(fault, *harness.members)
        await harness.start(Route.BACKLOG)
        await harness.advance(RESYNC_SECONDS)
        if replace:
            await harness.replace_room()

        harness.peer.post(CONTROL)
        await harness.advance(RESYNC_SECONDS)

        assert harness.adapter.controls == [CONTROL]
        assert harness.adapter.turns == [harness.members]
        assert harness.peer.statuses[CONTROL] == DeliveryStatus.PROCESSED
        assert harness.ctx.claims.pending_ack_ids(ROOM_ID) == list(harness.members)

    async def test_live_ordinary_input_waits_until_the_terminal_writes_recover(
        self, harness: Harness, ending: Ending, fault: Fault, replace: bool
    ) -> None:
        harness.peer.fail_terminals(fault, *harness.members)
        await harness.start(Route.BACKLOG)
        await harness.advance(RESYNC_SECONDS)
        if replace:
            await harness.replace_room()

        harness.peer.post(FOLLOWER)
        await harness.push(FOLLOWER)
        assert harness.adapter.turns == [harness.members]

        harness.heal()
        await harness.recover(Entrance.RECONCILIATION)

        assert harness.adapter.turns == [harness.members, (FOLLOWER,)]
        assert harness.member_statuses == dict.fromkeys(harness.members, ending.intent)
