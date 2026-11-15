"""A reconnect or an explicit resync request retries an unconfirmed write at once."""

from __future__ import annotations

import pytest

from band.platform.event import ReconnectedEvent
from tests.runtime.parking.harness import Cell, Harness, RoomPeer


@pytest.mark.looptime
class TestUnconfirmedResync:
    async def test_a_reconnect_retries_each_original_once_without_waiting(
        self, scenario: Cell, room_peer: RoomPeer, controlled_room: Harness
    ) -> None:
        await controlled_room.join_in_outage(scenario)

        await controlled_room.deliver(ReconnectedEvent())
        await controlled_room.elapse(1)

        assert room_peer.terminal_writes() == scenario.writes() * 2

    async def test_a_resync_request_retries_each_original_once_without_waiting(
        self, scenario: Cell, room_peer: RoomPeer, controlled_room: Harness
    ) -> None:
        await controlled_room.join_in_outage(scenario)

        await controlled_room.execution.request_resync()
        await controlled_room.elapse(1)

        assert room_peer.terminal_writes() == scenario.writes() * 2
