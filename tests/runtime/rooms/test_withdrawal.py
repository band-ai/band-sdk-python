"""A withdrawn room is unrouted at once and retired before it is forgotten."""

from __future__ import annotations

import asyncio

from band.runtime.rooms import RoomExecutions
from tests.runtime.helpers import RoomCleanup, StartGate
from tests.runtime.rooms.kit import Phase, phase, settle


class TestWithdrawal:
    async def test_leaving_a_serving_room_unroutes_it_before_cleanup_ends(
        self, rooms: RoomExecutions, room_cleanup: RoomCleanup
    ) -> None:
        await rooms.admit("room-1")
        room_cleanup.hold()

        rooms.withdraw("room-1")
        await room_cleanup.entered.wait()
        assert phase(rooms, "room-1") == Phase.OWNED

        room_cleanup.release.set()
        await settle(rooms, "room-1")
        assert phase(rooms, "room-1") == Phase.ABSENT
        assert room_cleanup.rooms == ["room-1"]

    async def test_leaving_during_a_held_start_retires_it_without_routing(
        self, rooms: RoomExecutions, start_gate: StartGate
    ) -> None:
        start_gate.hold()
        admitting = asyncio.create_task(rooms.admit("room-1"))
        await start_gate.entered.wait()

        rooms.withdraw("room-1")
        start_gate.release.set()

        assert await admitting is None
        (execution,) = start_gate.built
        assert execution.stopped and execution.resyncs == 0
        assert phase(rooms, "room-1") == Phase.ABSENT

    async def test_rejoining_during_cleanup_starts_fresh_once_it_ends(
        self, rooms: RoomExecutions, start_gate: StartGate, room_cleanup: RoomCleanup
    ) -> None:
        await rooms.admit("room-1")
        room_cleanup.hold()
        rooms.withdraw("room-1")
        await room_cleanup.entered.wait()

        rooms.request_admission("room-1")
        room_cleanup.release.set()
        await settle(rooms, "room-1")

        retired, fresh = start_gate.built
        assert retired.stopped and not fresh.stopped
        assert rooms.routed["room-1"] is fresh

    async def test_withdrawing_an_unknown_room_changes_nothing(
        self, rooms: RoomExecutions
    ) -> None:
        rooms.withdraw("room-1")

        assert await rooms.destroy("room-1") is True
        assert phase(rooms, "room-1") == Phase.ABSENT

    async def test_a_start_failing_after_withdrawal_is_retired_not_reported(
        self, rooms: RoomExecutions, start_gate: StartGate, room_cleanup: RoomCleanup
    ) -> None:
        start_gate.hold()
        start_gate.failures = 1
        admitting = asyncio.create_task(rooms.admit("room-1"))
        await start_gate.entered.wait()

        rooms.accepting = False
        destroying = asyncio.create_task(rooms.destroy("room-1"))
        start_gate.release.set()

        assert await destroying is True
        assert await admitting is None
        (failed,) = start_gate.built
        assert failed.stopped and room_cleanup.rooms == ["room-1"]
        assert phase(rooms, "room-1") == Phase.ABSENT

    async def test_events_for_a_withdrawn_room_are_not_replayed(
        self, rooms: RoomExecutions, start_gate: StartGate, room_cleanup: RoomCleanup
    ) -> None:
        start_gate.hold()
        admitting = asyncio.create_task(rooms.admit("room-1"))
        await start_gate.entered.wait()
        rooms.hold("room-1", "before leaving")
        rooms.withdraw("room-1")
        rooms.request_admission("room-1")

        start_gate.release.set()
        await admitting
        await settle(rooms, "room-1")
        assert [execution.events for execution in start_gate.built] == [[], []]
