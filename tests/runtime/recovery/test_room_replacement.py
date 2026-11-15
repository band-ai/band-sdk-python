"""Owed terminal writes belong to the runtime, so a replacement room retries them."""

from __future__ import annotations

import pytest

from tests.platform.settlement.kit import ROOM_ID
from tests.runtime.recovery.kit import (
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
class TestRoomReplacement:
    async def test_the_replacement_keeps_what_the_retired_room_owed(
        self, harness: Harness, fault: Fault
    ) -> None:
        harness.peer.fail_terminals(fault)
        await harness.start(Route.BACKLOG)
        retired = harness.ctx

        replacement = await harness.replace_room()

        assert replacement is not retired
        assert replacement.claims.pending_ack_ids(ROOM_ID) == list(harness.members)
        assert harness.model_turns == [harness.members]

    async def test_the_replacement_writes_the_stored_intent_and_never_the_model(
        self, harness: Harness, ending: Ending, fault: Fault
    ) -> None:
        harness.peer.fail_terminals(fault)
        await harness.start(Route.BACKLOG)
        await harness.replace_room()
        await harness.advance(RESYNC_SECONDS)
        harness.heal()

        await harness.recover(Entrance.RECONCILIATION)

        assert harness.model_turns == [harness.members]
        assert harness.recovery == {m: [ending.intent] for m in harness.members}
        assert harness.member_statuses == dict.fromkeys(harness.members, ending.intent)
        assert (harness.peer.reopened, harness.peer.rejected) == ([], [])
