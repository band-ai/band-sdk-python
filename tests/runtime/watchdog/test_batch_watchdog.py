"""A batch the cycle watchdog cuts off fails every original once and is not replayed."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from tests.runtime.recovery.kit import Fault, Harness
from tests.runtime.watchdog.kit import run_past_the_watchdog

pytestmark = pytest.mark.looptime


class TestBatchWatchdog:
    async def test_every_original_is_admitted_and_failed_once(
        self, harness: Harness
    ) -> None:
        await run_past_the_watchdog(harness)

        assert harness.peer.written_since(0) == {
            m: [DeliveryStatus.PROCESSING, DeliveryStatus.FAILED]
            for m in harness.members
        }
        assert harness.member_statuses == dict.fromkeys(
            harness.members, DeliveryStatus.FAILED
        )

    async def test_the_room_hears_one_failure(self, harness: Harness) -> None:
        await run_past_the_watchdog(harness)

        assert harness.peer.reports == [GENERIC_PROVIDER_FAILURE_MESSAGE]

    async def test_the_batch_is_not_replayed(self, harness: Harness) -> None:
        await run_past_the_watchdog(harness)

        assert harness.model_turns == [harness.members]

    @pytest.mark.parametrize("outage", list(Fault))
    async def test_an_unconfirmed_failed_write_is_retried_as_itself_not_by_the_model(
        self, harness: Harness, outage: Fault
    ) -> None:
        await run_past_the_watchdog(harness, outage=outage)

        assert harness.recovered_kinds == {DeliveryStatus.FAILED}
        assert harness.member_statuses == dict.fromkeys(
            harness.members, DeliveryStatus.FAILED
        )
        assert harness.model_turns == [harness.members]
