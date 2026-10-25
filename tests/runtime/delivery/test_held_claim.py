"""An original whose claim another owner holds is waited for, not opened or skipped."""

from __future__ import annotations

import pytest
from band_sdk_core import ClaimRegistry

from tests.runtime.delivery.kit import RestPeer, started_session
from tests.runtime.helpers import ROOM_ID

pytestmark = pytest.mark.looptime

BEFORE_THE_HELD_ONE = ([("m1",)], ["m1"])


@pytest.mark.parametrize(
    ("batching", "after_release"),
    [
        (True, [("m1",), ("m2", "m3")]),
        (False, [("m1",), ("m2",), ("m3",)]),
    ],
    ids=["batching", "singleton"],
)
class TestHeldClaim:
    async def test_nothing_behind_the_held_original_runs_until_it_is_released(
        self, rest_peer: RestPeer, batching: bool, after_release: list[tuple[str, ...]]
    ) -> None:
        events = [rest_peer.post(message_id) for message_id in ("m1", "m2", "m3")]
        claims = ClaimRegistry()
        claims.try_claim(ROOM_ID, "m2")

        async with started_session(
            rest_peer, batching=batching, claims=claims
        ) as session:
            await session.quiesce()
            waiting = session.progress
            await session.deliver(events[1])
            await session.resync()
            still_waiting = session.progress
            claims.release(ROOM_ID, "m2")
            await session.resync()

        assert (waiting, still_waiting, session.turns.ids) == (
            BEFORE_THE_HELD_ONE,
            BEFORE_THE_HELD_ONE,
            after_release,
        )
