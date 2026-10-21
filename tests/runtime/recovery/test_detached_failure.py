"""A turn released before its end is settled; its late failure is the adapter's to report."""

from __future__ import annotations

import band_sdk_core
import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.recovery.kit import (
    RESYNC_SECONDS,
    Ending,
    Harness,
    Route,
    Shape,
)

pytestmark = pytest.mark.looptime


@pytest.mark.parametrize("shape", [Shape.SINGLETON, Shape.BATCH])
@pytest.mark.parametrize("ending", [Ending.DETACHES])
class TestDetachedLateFailure:
    async def test_the_released_originals_are_already_processed(
        self, harness: Harness
    ) -> None:
        await harness.start(Route.BACKLOG)

        assert harness.peer.written_since(0) == {
            m: [DeliveryStatus.PROCESSING, DeliveryStatus.PROCESSED]
            for m in harness.members
        }
        assert harness.peer.reports == []

    async def test_a_late_failure_is_reported_once_by_the_adapter_and_changes_nothing(
        self, harness: Harness
    ) -> None:
        await harness.start(Route.BACKLOG)

        harness.adapter.late_failure.set()
        await harness.drain()
        await harness.advance(RESYNC_SECONDS)

        assert harness.peer.reports == [band_sdk_core.missing_reply_message()]
        assert harness.member_statuses == dict.fromkeys(
            harness.members, DeliveryStatus.PROCESSED
        )
        assert harness.peer.write_kinds == {
            DeliveryStatus.PROCESSING,
            DeliveryStatus.PROCESSED,
        }
        assert harness.model_turns == [harness.members]
