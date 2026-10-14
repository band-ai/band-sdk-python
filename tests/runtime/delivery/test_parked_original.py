"""A parked terminal write leaves its original alone and other inputs running."""

from __future__ import annotations

import pytest

from tests.platform.settlement.kit import MISSING_ATTEMPT
from tests.runtime.delivery.kit import RestPeer, started_session


@pytest.mark.looptime
@pytest.mark.parametrize("batching", [True, False], ids=["batching", "singleton"])
class TestParkedOriginal:
    async def test_other_inputs_run_and_the_parked_original_is_never_reopened(
        self, peer: RestPeer, batching: bool
    ) -> None:
        first = peer.post("m1")
        peer.terminal_error = MISSING_ATTEMPT

        async with started_session(peer, batching=batching) as session:
            await session.quiesce()
            peer.terminal_error = None
            await session.deliver(first, peer.post("m2"))
            await session.resync()

        assert (session.progress, peer.runaway) == (
            ([("m1",), ("m2",)], ["m1", "m2"]),
            False,
        )
