"""A 404 while the room is readable means the message is gone: its write is never resent."""

from __future__ import annotations

from dataclasses import replace

import pytest

from band.platform.event import ReconnectedEvent
from tests.runtime.parking.harness import (
    PAST_TWO_INTERVALS,
    Cell,
    Harness,
    Refusal,
    RoomPeer,
)


@pytest.mark.looptime
class TestGoneMessage:
    async def test_neither_a_reconnect_nor_a_rejoin_resends_the_terminal_write(
        self, scenario: Cell, room_peer: RoomPeer, room: Harness
    ) -> None:
        room_peer.refuse(Refusal.DELETED, *scenario.queued)
        await room.join()
        await room.attempted_terminal_writes(len(scenario.queued))

        await room.deliver(ReconnectedEvent())
        await room.elapse(1)
        await room.leave()
        await room.join()
        await room.elapse(PAST_TWO_INTERVALS)

        # A rejoined room starts a fresh local ledger.
        assert room.outcome() == replace(scenario.after_gone(), completed=[])
