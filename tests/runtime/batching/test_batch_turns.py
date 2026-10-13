"""Queued ordinary messages run as one turn, oldest first, up to the ceiling."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.runtime.batching.kit import FakeRoom, started_room


class TestBatchTurns:
    @pytest.mark.asyncio
    async def test_messages_queued_behind_a_busy_turn_run_together_in_order(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        async with started_room(platform, mock_rest_client, gate=True) as room:
            await room.deliver(platform.post("m1"))
            await room.until(lambda: room.turns.ids == [("m1",)])
            await room.deliver(
                platform.post("m2"), platform.post("m3"), platform.post("m4")
            )
            room.turns.release.set()
            await room.settled("m1", "m2", "m3", "m4")

        assert room.turns.ids == [("m1",), ("m2", "m3", "m4")]

    @pytest.mark.asyncio
    async def test_the_ceiling_bounds_each_turn(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        for message_id in ("m1", "m2", "m3", "m4", "m5"):
            platform.post(message_id)

        async with started_room(platform, mock_rest_client, max_queued=2) as room:
            await room.settled("m1", "m2", "m3", "m4", "m5")

        assert room.turns.ids == [("m1", "m2"), ("m3", "m4"), ("m5",)]

    @pytest.mark.asyncio
    async def test_without_batching_each_message_is_its_own_turn(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        async with started_room(platform, mock_rest_client, batching=False) as room:
            await room.deliver(
                platform.post("m1"), platform.post("m2"), platform.post("m3")
            )
            await room.settled("m1", "m2", "m3")

        assert room.turns.ids == [("m1",), ("m2",), ("m3",)]
