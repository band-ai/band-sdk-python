"""A retry original whose terminal write cannot confirm never starves roster events."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.conftest import make_participant_added_event
from tests.platform.settlement.kit import MISSING_ATTEMPT
from tests.runtime.delivery.kit import RestPeer, started_session
from tests.runtime.helpers import ROOM_ID

pytestmark = pytest.mark.looptime


class TestParkedRetryRoster:
    @pytest.mark.parametrize("parked", [False, True], ids=["uncertain", "parked"])
    async def test_a_roster_event_queued_behind_the_original_is_applied(
        self, parked: bool
    ) -> None:
        peer = RestPeer()
        peer.confirm_terminal = False
        peer.terminal_error = MISSING_ATTEMPT if parked else None
        async with started_session(
            peer, retries=3, fail_on=frozenset({"m1"})
        ) as session:
            await session.deliver(peer.post("m1", listed=False))
            await session.reached(DeliveryStatus.PROCESSING, "m1")
            await session.resync()
            await session.deliver(
                make_participant_added_event(
                    room_id=ROOM_ID, participant_id="user-456", name="New", type="User"
                )
            )
            await session.until(
                lambda: "user-456" in [p["id"] for p in session.ctx.participants]
            )
