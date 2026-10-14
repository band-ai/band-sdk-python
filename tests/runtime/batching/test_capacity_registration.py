"""A room competes for shared capacity only while capacity alone blocks its input."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.runtime.batching.kit import FakeRoom, SharedCapacity, started_room


class TestCapacityRegistration:
    @pytest.mark.asyncio
    async def test_a_refused_input_registers_and_each_retry_keeps_one_generation(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        capacity = SharedCapacity()

        async with started_room(platform, mock_rest_client, capacity=capacity) as room:
            room.turns.accepting = False
            await room.deliver(platform.post("m1"))
            await room.until(lambda: "register" in capacity.actions())
            await room.ctx.request_resync()
            await room.until(lambda: capacity.actions().count("register") >= 2)

        assert len(capacity.generations()) == 1

    @pytest.mark.asyncio
    async def test_running_the_input_withdraws_the_request(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        capacity = SharedCapacity()

        async with started_room(platform, mock_rest_client, capacity=capacity) as room:
            room.turns.accepting = False
            await room.deliver(platform.post("m1"))
            await room.until(lambda: "register" in capacity.actions())
            room.turns.accepting = True
            await room.ctx.request_resync()
            await room.settled("m1")

        assert (capacity.actions()[0], capacity.actions()[-1]) == (
            "register",
            "withdraw",
        )

    @pytest.mark.asyncio
    async def test_stopping_the_room_withdraws_the_request(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        capacity = SharedCapacity()

        async with started_room(platform, mock_rest_client, capacity=capacity) as room:
            room.turns.accepting = False
            await room.deliver(platform.post("m1"))
            await room.until(lambda: "register" in capacity.actions())
            room.ctx.stop_room()
            withdrawn = capacity.actions()[-1]

        assert withdrawn == "withdraw"

    @pytest.mark.asyncio
    async def test_an_input_that_runs_at_once_never_registers(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        capacity = SharedCapacity()

        async with started_room(platform, mock_rest_client, capacity=capacity) as room:
            await room.deliver(platform.post("m1"))
            await room.settled("m1")

        assert "register" not in capacity.actions()

    @pytest.mark.asyncio
    async def test_a_replacement_room_uses_its_own_generation(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        capacity = SharedCapacity()

        for _ in range(2):
            async with started_room(
                platform, mock_rest_client, capacity=capacity
            ) as room:
                room.turns.accepting = False
                await room.deliver(platform.post("m1"))
                await room.until(lambda: "register" in capacity.actions())

        assert len(capacity.generations()) == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize("blocker", ["terminal-write", "processing"])
    async def test_a_room_blocked_by_something_else_stops_competing(
        self, platform: FakeRoom, mock_rest_client: MagicMock, blocker: str
    ) -> None:
        capacity = SharedCapacity()

        async with started_room(platform, mock_rest_client, capacity=capacity) as room:
            room.turns.accepting = False
            await room.deliver(platform.post("m1"))
            await room.until(lambda: "register" in capacity.actions())
            room.turns.accepting = True
            platform.unconfirmed_terminal = blocker == "terminal-write"
            if blocker == "processing":
                platform.refuse_processing.add("m1")
            registrations = capacity.actions().count("register")
            await room.ctx.request_resync()
            await room.until(lambda: capacity.actions()[-1] == "withdraw")

        assert capacity.actions().count("register") == registrations
