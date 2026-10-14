"""Fixtures: a room peer holding the queued messages and a runtime serving it."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from band_rest.core import http_client

from tests.runtime.parking.harness import (
    CELLS,
    Cell,
    Harness,
    RoomPeer,
    harness_over,
)


@pytest.fixture(autouse=True)
def instant_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the generated client's retry count but not its multi-second backoff."""
    monkeypatch.setattr(http_client, "_retry_timeout", lambda **_: 0.0)


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
