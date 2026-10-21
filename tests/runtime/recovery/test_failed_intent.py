"""A stored intent is retried as itself: failed keeps its error, processed stays processed."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from tests.runtime.recovery.kit import (
    TURNS,
    Ending,
    Entrance,
    Fault,
    Harness,
    Shape,
)

pytestmark = pytest.mark.looptime

RETRY_PASSES = 3


@pytest.mark.parametrize("fault", list(Fault))
class TestStoredIntentRetry:
    @pytest.mark.parametrize(("shape", "ending"), TURNS)
    async def test_every_retry_is_the_stored_kind_of_write(
        self, harness: Harness, ending: Ending, fault: Fault
    ) -> None:
        await harness.outage_then(fault, Entrance.RECONCILIATION, passes=RETRY_PASSES)

        retries = [
            harness.peer.terminal_requests(m) // fault.requests_per_write
            for m in harness.members
        ]
        assert min(retries) > RETRY_PASSES
        assert harness.peer.write_kinds == {DeliveryStatus.PROCESSING, ending.intent}
        assert harness.member_statuses == dict.fromkeys(harness.members, ending.intent)

    @pytest.mark.parametrize("shape", [Shape.OFF])
    @pytest.mark.parametrize("ending", [Ending.RAISES])
    async def test_a_failed_original_keeps_its_stored_error(
        self, harness: Harness, fault: Fault
    ) -> None:
        await harness.outage_then(fault, Entrance.RECONCILIATION, passes=RETRY_PASSES)

        sent = {error for errors in harness.peer.errors.values() for error in errors}
        assert sent == {GENERIC_PROVIDER_FAILURE_MESSAGE}
        assert harness.model_turns == [harness.members]
