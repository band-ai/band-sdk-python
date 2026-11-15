"""A message another owner is running holds the room's order until it lets go."""

from __future__ import annotations

import pytest
from band_sdk_core import ClaimRegistry

from tests.runtime.delivery.kit import RestPeer, started_session
from tests.runtime.helpers import ROOM_ID

pytestmark = pytest.mark.looptime


class TestHeldClaim:
    async def test_later_messages_wait_behind_a_claim_held_elsewhere(self) -> None:
        peer = RestPeer()
        claims = ClaimRegistry()
        async with started_session(peer, claims=claims) as session:
            await session.quiesce()
            held = peer.post("held", at=1)
            later = peer.post("later", at=2)
            assert claims.try_claim(ROOM_ID, "held") is not None

            await session.deliver(held, later)
            await session.quiesce()
            assert session.turns.ids == []

            claims.release(ROOM_ID, "held")
            peer.set_status("held", None)
            await session.resync()
            await session.settled("held", "later")

        assert session.turns.ids == [("held",), ("later",)]
