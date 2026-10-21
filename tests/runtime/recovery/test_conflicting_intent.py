"""The first stored terminal intent wins: a conflicting one changes nothing."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from tests.platform.settlement.kit import ROOM_ID
from tests.runtime.recovery.kit import (
    TURNS,
    Ending,
    Entrance,
    Fault,
    Harness,
    Route,
)

pytestmark = pytest.mark.looptime

OTHER_ERROR = "another error"


@pytest.mark.parametrize(("shape", "ending"), TURNS)
class TestConflictingIntent:
    async def test_the_stored_intent_survives_an_attempt_to_replace_it(
        self, harness: Harness, ending: Ending
    ) -> None:
        harness.peer.fail_terminals(Fault.LOST)
        await harness.start(Route.BACKLOG)
        claims, member = harness.ctx.claims, harness.members[0]

        claims.remember_ack_pending(ROOM_ID, member)
        with pytest.raises(ValueError, match="different pending terminal intent"):
            claims.remember_failed_ack_pending(ROOM_ID, member, OTHER_ERROR)

        owed = claims.pending_ack(ROOM_ID, member)
        assert owed is not None
        assert owed["status"] == ending.intent
        assert owed.get("error") == (
            GENERIC_PROVIDER_FAILURE_MESSAGE
            if ending.intent is DeliveryStatus.FAILED
            else None
        )

    async def test_the_platform_only_ever_hears_the_first_intent(
        self, harness: Harness, ending: Ending
    ) -> None:
        harness.peer.fail_terminals(Fault.LOST)
        await harness.start(Route.BACKLOG)
        harness.ctx.claims.remember_ack_pending(ROOM_ID, harness.members[0])
        harness.heal()

        await harness.recover(Entrance.RECONCILIATION)

        assert harness.recovered_kinds == {ending.intent}
        assert harness.peer.rejected == []
