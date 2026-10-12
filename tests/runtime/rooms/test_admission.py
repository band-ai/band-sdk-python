"""A wanted room starts one execution, routes it and recovers missed work."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.rooms import MAX_HELD_EVENTS, RoomExecutions
from tests.runtime.helpers import RoomCleanup, StartGate
from tests.runtime.rooms.kit import Phase, phase, settle


class TestAdmission:
    async def test_a_wanted_room_is_routed_and_resyncs_missed_work(
        self, rooms: RoomExecutions, start_gate: StartGate
    ) -> None:
        execution = await rooms.admit("room-1")

        assert phase(rooms, "room-1") == Phase.SERVING
        assert start_gate.built == [execution]
        assert execution.resyncs == 1

    async def test_a_failed_start_stays_owned_until_a_signal_restarts_it(
        self, rooms: RoomExecutions, start_gate: StartGate, room_cleanup: RoomCleanup
    ) -> None:
        start_gate.failures = 1
        with pytest.raises(RuntimeError, match="execution start failed"):
            await rooms.admit("room-1")
        assert phase(rooms, "room-1") == Phase.OWNED

        rooms.converge("room-1")
        await settle(rooms, "room-1")

        failed, replacement = start_gate.built
        assert phase(rooms, "room-1") == Phase.SERVING
        assert failed.stopped and room_cleanup.rooms == ["room-1"]
        assert rooms.routed["room-1"] is replacement

    async def test_a_stop_that_overtakes_a_held_start_retires_it_unrouted(
        self, rooms: RoomExecutions, start_gate: StartGate, room_cleanup: RoomCleanup
    ) -> None:
        start_gate.hold()
        admitting = asyncio.create_task(rooms.admit("room-1"))
        await start_gate.entered.wait()

        rooms.accepting = False
        destroying = asyncio.create_task(rooms.destroy("room-1"))
        start_gate.release.set()

        assert await destroying is True
        assert await admitting is None
        (execution,) = start_gate.built
        assert execution.stopped and execution.resyncs == 0
        assert room_cleanup.rooms == ["room-1"]
        assert phase(rooms, "room-1") == Phase.ABSENT

    async def test_events_during_startup_reach_the_execution_in_order(
        self, rooms: RoomExecutions, start_gate: StartGate
    ) -> None:
        start_gate.hold()
        admitting = asyncio.create_task(rooms.admit("room-1"))
        await start_gate.entered.wait()

        rooms.hold("room-1", "participant added")
        rooms.hold("room-1", "message created")
        start_gate.release.set()

        execution = await admitting
        assert execution.events == ["participant added", "message created"]
        assert execution.resyncs == 1

    async def test_startup_delivery_keeps_events_that_arrive_while_a_full_queue_drains(
        self,
    ) -> None:
        delivered: list[object] = []
        entered, release = asyncio.Event(), asyncio.Event()

        class Execution:
            async def start(self) -> None:
                entered.set()
                await release.wait()

            async def on_event(self, event: object) -> None:
                delivered.append(event)
                await asyncio.sleep(0)
                if isinstance(event, int):
                    owner.hold("room-1", f"e{event}")
                elif event == "e0":
                    owner.hold("room-1", "a")
                    owner.hold("room-1", "b")

            async def stop(self, timeout: float | None = None) -> bool:
                return True

        async def release_room(room_id: str) -> None:
            return None

        owner = RoomExecutions(build=lambda room_id: Execution(), release=release_room)
        admitting = asyncio.create_task(owner.admit("room-1"))
        await entered.wait()
        for index in range(MAX_HELD_EVENTS):
            owner.hold("room-1", index)
        release.set()
        await admitting
        assert "e1" in delivered
        assert "a" in delivered and "b" in delivered
        await owner.destroy("room-1")
