"""Fixtures: a room peer holding the queued messages and a runtime serving it."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.runtime.batching.kit import ControlScript
from tests.runtime.parking.harness import (
    CELLS,
    Cell,
    Harness,
    RoomPeer,
    harness_over,
)


@pytest.fixture(params=CELLS, ids=lambda scenario: scenario.id)
def scenario(request: pytest.FixtureRequest) -> Cell:
    return request.param


@pytest.fixture
def room_peer(scenario: Cell) -> RoomPeer:
    room_peer = RoomPeer()
    for message_id in scenario.mode.queued:
        room_peer.post(message_id)
    return room_peer


@pytest.fixture
async def room(room_peer: RoomPeer, scenario: Cell) -> AsyncIterator[Harness]:
    async with harness_over(room_peer, scenario) as harness:
        yield harness


@pytest.fixture
async def controlled_room(
    room_peer: RoomPeer, scenario: Cell
) -> AsyncIterator[Harness]:
    """The same room served by a host that scans every arrival for controls."""
    async with harness_over(room_peer, scenario, controls=ControlScript()) as harness:
        yield harness
