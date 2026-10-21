"""A retained original deleted before its turn leaves the room, not the head of its line."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.delivery.kit import RestPeer, started_session, write

pytestmark = pytest.mark.looptime


class TestDeletedOriginal:
    async def test_deleted_message_is_released_and_later_input_runs_at_once(
        self,
    ) -> None:
        peer = RestPeer()
        async with started_session(peer, gate=True) as session:
            await session.quiesce()
            await session.deliver(peer.post("warmup", at=1))
            await session.until(lambda: session.turns.ids == [("warmup",)])
            await session.deliver(peer.post("deleted", at=2))
            # The sender deletes it while the agent is busy with the warmup turn.
            peer.delete("deleted")
            session.turns.gate = False
            session.turns.release.set()
            await session.settled("warmup")
            await session.quiesce()
            before_live = len(peer.journal)

            await session.deliver(peer.post("live", at=3), peer.post("later", at=4))
            await session.quiesce()

        assert session.turns.ids == [("warmup",), ("live",), ("later",)]
        assert peer.status_of("later") is DeliveryStatus.PROCESSED
        # Later input does not re-claim the deleted message.
        assert (
            write(DeliveryStatus.PROCESSING, "deleted", confirmed=False)
            not in peer.journal[before_live:]
        )
