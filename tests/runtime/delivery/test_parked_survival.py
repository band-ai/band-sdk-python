"""A parked original stays unopened without leaning on the retry budget."""

from __future__ import annotations

import pytest

from tests.platform.settlement.kit import MISSING_ATTEMPT
from tests.runtime.delivery.kit import RestPeer, started_session

pytestmark = pytest.mark.looptime


class TestParkedSurvival:
    async def test_a_parked_original_is_skipped_once_its_retry_charge_is_forgotten(
        self, rest_peer: RestPeer
    ) -> None:
        rest_peer.post("m1")
        rest_peer.terminal_error = MISSING_ATTEMPT

        async with started_session(rest_peer, batching=True) as session:
            await session.quiesce()
            session.ctx._retry_tracker.discard_attempt("m1")
            await session.resync()

        assert session.progress == ([("m1",)], ["m1"])
