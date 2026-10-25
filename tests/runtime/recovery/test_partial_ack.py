"""A shared turn whose writes are only partly confirmed keeps the rest owed."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.recovery.kit import (
    RESYNC_SECONDS,
    Ending,
    Entrance,
    Fault,
    Harness,
    Route,
    Shape,
)

pytestmark = pytest.mark.looptime

UNCONFIRMED = "m2"
CONFIRMED = ("m1", "m3")


@pytest.mark.parametrize("shape", [Shape.BATCH])
class TestPartialAck:
    @pytest.mark.parametrize(
        ("ending", "fault", "unconfirmed_status"),
        [
            pytest.param(
                Ending.DECLINES,
                Fault.LOST,
                DeliveryStatus.PROCESSED,
                id="decline-applied-response-lost",
            ),
            pytest.param(
                Ending.RAISES,
                Fault.LOST,
                DeliveryStatus.FAILED,
                id="failure-applied-response-lost",
            ),
            pytest.param(
                Ending.DECLINES,
                Fault.REFUSED,
                DeliveryStatus.PROCESSING,
                id="decline-refused",
            ),
            pytest.param(
                Ending.RAISES,
                Fault.REFUSED,
                DeliveryStatus.PROCESSING,
                id="failure-refused",
            ),
        ],
    )
    async def test_the_confirmed_originals_are_settled_while_one_is_owed(
        self,
        harness: Harness,
        ending: Ending,
        fault: Fault,
        unconfirmed_status: DeliveryStatus,
    ) -> None:
        harness.peer.fail_terminals(fault, UNCONFIRMED)

        await harness.start(Route.BACKLOG)

        assert harness.peer.statuses == {
            "m1": ending.intent,
            UNCONFIRMED: unconfirmed_status,
            "m3": ending.intent,
        }
        assert harness.model_turns == [harness.members]

    @pytest.mark.parametrize("ending", [Ending.DECLINES, Ending.RAISES])
    @pytest.mark.parametrize("fault", list(Fault))
    async def test_only_the_unconfirmed_original_is_written_again(
        self, harness: Harness, ending: Ending, fault: Fault
    ) -> None:
        harness.peer.fail_terminals(fault, UNCONFIRMED)
        await harness.start(Route.BACKLOG)
        await harness.advance(RESYNC_SECONDS)
        harness.heal()

        await harness.recover(Entrance.RECONCILIATION)

        written = harness.peer.written_since(0)
        assert [written[m] for m in CONFIRMED] == [["processing", ending.intent]] * 2
        assert harness.recovery == {UNCONFIRMED: [ending.intent]}
        assert harness.member_statuses == dict.fromkeys(harness.members, ending.intent)
        assert harness.model_turns == [harness.members]
