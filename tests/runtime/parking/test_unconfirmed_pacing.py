"""An unconfirmed terminal write is retried once per interval, whatever arrives."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import InputNotAccepted
from tests.runtime.conftest import Waiting
from tests.runtime.deferral.kit import callbacks_finished
from tests.runtime.parking.harness import (
    OTHER,
    RECONCILE_SECONDS,
    REFUSED,
    Cell,
    Harness,
    RoomPeer,
)

ARRIVALS = 5
INTERVALS = 2


@pytest.mark.looptime
class TestUnconfirmedPacing:
    async def test_admission_readiness_preserves_the_terminal_write_deadline(
        self, scenario: Cell, room_peer: RoomPeer, controlled_room: Harness
    ) -> None:
        room_peer.script(REFUSED, *scenario.queued)
        await controlled_room.join()
        await controlled_room.attempted_terminal_writes(len(scenario.queued))
        await Waiting.suspended(controlled_room.execution)
        deadline = room_peer.terminal_times[0] + RECONCILE_SECONDS
        await controlled_room.elapse(deadline - asyncio.get_running_loop().time() - 1)
        await Waiting.suspended(controlled_room.execution)
        room_peer.message_read.clear()
        blocker = asyncio.get_running_loop().create_future()
        controlled_room.execution.defer_delivery(
            None, reason=InputNotAccepted(ready=blocker)
        )
        blocker.set_result(None)
        await callbacks_finished()
        await room_peer.message_read.wait()
        await Waiting.suspended(controlled_room.execution)
        assert room_peer.terminal_writes() == scenario.writes()

        room_peer.relent()
        await controlled_room.elapse(deadline - asyncio.get_running_loop().time())
        await controlled_room.intents_resolved()
        assert controlled_room.outcome() == scenario.after_acceptance(rounds=2)
        assert room_peer.attempt_offsets() == [0, RECONCILE_SECONDS]

    async def test_the_first_attempt_is_the_only_write_before_the_interval_elapses(
        self, scenario: Cell, room_peer: RoomPeer, controlled_room: Harness
    ) -> None:
        await controlled_room.join_in_outage(scenario)

        assert room_peer.terminal_writes() == scenario.writes()

    async def test_inputs_arriving_within_the_interval_cost_no_terminal_write(
        self, scenario: Cell, room_peer: RoomPeer, controlled_room: Harness
    ) -> None:
        await controlled_room.join_in_outage(scenario)

        for arrival in range(ARRIVALS):
            await controlled_room.deliver(room_peer.post(f"{OTHER}-{arrival}"))
            await controlled_room.elapse(1)

        assert room_peer.terminal_writes() == scenario.writes()

    async def test_each_interval_retries_each_original_once_however_many_inputs_arrive(
        self, scenario: Cell, room_peer: RoomPeer, controlled_room: Harness
    ) -> None:
        await controlled_room.join_in_outage(scenario)

        for interval in range(INTERVALS):
            await controlled_room.deliver(room_peer.post(f"{OTHER}-{interval}"))
            await controlled_room.elapse(RECONCILE_SECONDS)

        assert room_peer.terminal_writes() == scenario.writes() * (INTERVALS + 1)

    async def test_recovery_after_the_outage_needs_no_arrival(
        self, scenario: Cell, room_peer: RoomPeer, controlled_room: Harness
    ) -> None:
        await controlled_room.join_in_outage(scenario)

        room_peer.relent()
        await controlled_room.elapse(RECONCILE_SECONDS)
        await controlled_room.intents_resolved()

        assert controlled_room.outcome().once_each() == scenario.after_acceptance()
