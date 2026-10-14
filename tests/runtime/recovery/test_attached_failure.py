"""An attached turn that fails after its effects fails every original, once."""

from __future__ import annotations

import band_sdk_core
import pytest

from band.client.streaming import DeliveryStatus
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from tests.runtime.recovery.kit import (
    RESYNC_SECONDS,
    SECRET,
    Ending,
    Entrance,
    Harness,
    Route,
    Shape,
)

pytestmark = pytest.mark.looptime

# Who tells the room: the runtime for a model that raised, the judge for a turn
# that ended without replying. Either way the room hears it once.
REPORTS = [
    (Ending.RAISES, GENERIC_PROVIDER_FAILURE_MESSAGE),
    (Ending.SILENT, band_sdk_core.missing_reply_message()),
]


@pytest.mark.parametrize("shape", [Shape.SINGLETON, Shape.BATCH])
@pytest.mark.parametrize(("ending", "report"), REPORTS)
class TestAttachedFailure:
    async def test_every_original_fails_with_the_same_error(
        self, harness: Harness, ending: Ending, report: str
    ) -> None:
        await harness.start(Route.BACKLOG)

        assert harness.peer.written_since(0) == {
            m: [DeliveryStatus.PROCESSING, DeliveryStatus.FAILED]
            for m in harness.members
        }
        assert harness.peer.errors == {
            m: [GENERIC_PROVIDER_FAILURE_MESSAGE] for m in harness.members
        }

    async def test_the_room_hears_one_failure_and_never_the_exception(
        self, harness: Harness, ending: Ending, report: str
    ) -> None:
        await harness.start(Route.BACKLOG)

        assert harness.peer.reports == [report]
        assert SECRET not in str(harness.peer.events)

    async def test_the_turn_is_not_replayed(
        self, harness: Harness, ending: Ending, report: str
    ) -> None:
        await harness.start(Route.BACKLOG)

        await harness.recover(Entrance.RECONCILIATION)
        await harness.advance(RESYNC_SECONDS)

        assert harness.model_turns == [harness.members]
        assert harness.member_statuses == dict.fromkeys(
            harness.members, DeliveryStatus.FAILED
        )
        assert harness.peer.reports == [report]


@pytest.mark.parametrize("shape", [Shape.SINGLETON, Shape.BATCH])
@pytest.mark.parametrize("ending", [Ending.RAISES])
class TestAttachedEffects:
    async def test_the_reply_posted_before_the_failure_stands_once(
        self, harness: Harness
    ) -> None:
        await harness.start(Route.BACKLOG)
        await harness.recover(Entrance.RECONCILIATION)
        await harness.advance(RESYNC_SECONDS)

        assert [reply["content"] for reply in harness.peer.replies] == ["done"]
