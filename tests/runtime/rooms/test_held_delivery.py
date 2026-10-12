"""Held startup events reach only the admission they were held for."""

from __future__ import annotations

import asyncio

from band.runtime.rooms import RoomExecutions
from tests.runtime.helpers import StartGate
from tests.runtime.rooms.kit import Phase, deliver_first_of_two, phase, settle


class TestHeldDeliveryAuthority:
    async def test_a_leave_stops_delivery_and_retires_the_start_unrouted(
        self,
        rooms: RoomExecutions,
        start_gate: StartGate,
    ) -> None:
        delivering = await deliver_first_of_two(rooms, start_gate)
        rooms.withdraw("room-1")
        start_gate.deliver.set()

        assert await delivering is None
        (stale,) = start_gate.built
        assert stale.events == ["early"]
        assert stale.resyncs == 0
        assert start_gate.journal == ["start 1", "stop 1", "cleanup room-1"]
        assert phase(rooms, "room-1") == Phase.ABSENT

    async def test_a_stop_stops_delivery_and_retires_the_start_unrouted(
        self,
        rooms: RoomExecutions,
        start_gate: StartGate,
    ) -> None:
        delivering = await deliver_first_of_two(rooms, start_gate)
        rooms.accepting = False
        destroying = asyncio.create_task(rooms.destroy("room-1"))
        start_gate.deliver.set()

        assert await destroying is True
        assert await delivering is None
        (stale,) = start_gate.built
        assert stale.events == ["early"]
        assert stale.resyncs == 0
        assert start_gate.journal == ["start 1", "stop 1", "cleanup room-1"]
        assert phase(rooms, "room-1") == Phase.ABSENT

    async def test_a_rejoin_retires_the_stale_start_before_a_fresh_one_serves(
        self,
        rooms: RoomExecutions,
        start_gate: StartGate,
    ) -> None:
        delivering = await deliver_first_of_two(rooms, start_gate)
        rooms.withdraw("room-1")
        rooms.request_admission("room-1")
        rooms.hold("room-1", "after rejoin")
        start_gate.deliver.set()
        await delivering
        await settle(rooms, "room-1")

        stale, fresh = start_gate.built
        assert stale.events == ["early"] and stale.resyncs == 0
        assert fresh.events == ["after rejoin"] and fresh.resyncs == 1
        assert start_gate.journal == [
            "start 1",
            "stop 1",
            "cleanup room-1",
            "start 2",
        ]
        assert rooms.routed["room-1"] is fresh
        assert phase(rooms, "room-1") == Phase.SERVING
