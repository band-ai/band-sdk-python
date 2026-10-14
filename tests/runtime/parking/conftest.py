"""Fixtures: a room peer holding the queued messages and a runtime serving it."""

from __future__ import annotations

import random
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


@pytest.fixture(autouse=True)
def unjittered_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a room's first periodic reconciliation fall a full interval after start."""
    monkeypatch.setattr(random, "uniform", lambda lower, upper: upper)


@pytest.fixture(params=CELLS, ids=lambda cell: cell.id)
def cell(request: pytest.FixtureRequest) -> Cell:
    return request.param


@pytest.fixture
def peer(cell: Cell) -> RoomPeer:
    peer = RoomPeer()
    for message_id in cell.mode.queued:
        peer.post(message_id)
    return peer


@pytest.fixture
async def room(peer: RoomPeer, cell: Cell) -> AsyncIterator[Harness]:
    async with harness_over(peer, cell) as harness:
        yield harness
