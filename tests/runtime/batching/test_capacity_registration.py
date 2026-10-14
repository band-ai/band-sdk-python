"""A room competes for shared capacity only while capacity alone blocks its input."""

from __future__ import annotations

import pytest

from tests.runtime.batching.kit import (
    CapacityAction,
    FakeRoom,
    SharedCapacity,
    started_room,
)


class TestCapacityRegistration:
    async def test_a_refused_input_registers_and_each_retry_keeps_one_generation(
        self, platform: FakeRoom
    ) -> None:
        capacity = SharedCapacity()

        async with started_room(platform, capacity=capacity) as room:
            await room.queue_for_capacity("m1", capacity)
            await room.ctx.request_resync()
            await room.until(lambda: capacity.registrations >= 2)

        assert len(capacity.generations()) == 1

    async def test_running_the_input_withdraws_the_request(
        self, platform: FakeRoom
    ) -> None:
        capacity = SharedCapacity()

        async with started_room(platform, capacity=capacity) as room:
            await room.queue_for_capacity("m1", capacity)
            room.turns.accepting = True
            await room.ctx.request_resync()
            await room.settled("m1")

        assert (capacity.actions()[0], capacity.actions()[-1]) == (
            CapacityAction.REGISTER,
            CapacityAction.WITHDRAW,
        )

    async def test_stopping_the_room_withdraws_the_request(
        self, platform: FakeRoom
    ) -> None:
        capacity = SharedCapacity()

        async with started_room(platform, capacity=capacity) as room:
            await room.queue_for_capacity("m1", capacity)
            room.ctx.stop_room()
            withdrawn = capacity.actions()[-1]

        assert withdrawn == CapacityAction.WITHDRAW

    async def test_an_input_that_runs_at_once_never_registers(
        self, platform: FakeRoom
    ) -> None:
        capacity = SharedCapacity()

        async with started_room(platform, capacity=capacity) as room:
            await room.deliver(platform.post("m1"))
            await room.settled("m1")

        assert capacity.registrations == 0

    async def test_a_replacement_room_uses_its_own_generation(
        self, platform: FakeRoom
    ) -> None:
        capacity = SharedCapacity()

        for _ in range(2):
            async with started_room(platform, capacity=capacity) as room:
                await room.queue_for_capacity("m1", capacity)

        assert len(capacity.generations()) == 2

    @pytest.mark.parametrize(
        ("unconfirmed_terminal", "refused"),
        [(True, set()), (False, {"m1"})],
        ids=["terminal-write", "processing"],
    )
    async def test_a_room_blocked_by_something_else_stops_competing(
        self, platform: FakeRoom, unconfirmed_terminal: bool, refused: set[str]
    ) -> None:
        capacity = SharedCapacity()

        async with started_room(platform, capacity=capacity) as room:
            await room.queue_for_capacity("m1", capacity)
            room.turns.accepting = True
            platform.unconfirmed_terminal = unconfirmed_terminal
            platform.refuse_processing |= refused
            registrations = capacity.registrations
            await room.ctx.request_resync()
            await room.until(lambda: capacity.actions()[-1] == CapacityAction.WITHDRAW)

        assert capacity.registrations == registrations
