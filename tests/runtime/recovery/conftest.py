"""Fixtures: the real runtime wired to a stateful room peer."""

from __future__ import annotations

import random
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from tests.platform.settlement.conftest import instant_retries
from tests.runtime.recovery.kit import (
    Ending,
    Harness,
    RoomPeer,
    ScriptedAdapter,
    Shape,
    wired_harness,
)

__all__ = ["instant_retries"]


@pytest.fixture(autouse=True)
def first_resync_at_the_base_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the room's reconciliation clock deterministic."""
    monkeypatch.setattr(random, "uniform", lambda lower, upper: upper)


@pytest.fixture(params=list(Shape))
def shape(request: pytest.FixtureRequest) -> Shape:
    return request.param


@pytest.fixture
def ending() -> Ending:
    return Ending.DECLINES


@pytest.fixture
def peer() -> RoomPeer:
    return RoomPeer()


@pytest.fixture
def adapter(ending: Ending) -> ScriptedAdapter:
    scripted = ScriptedAdapter()
    scripted.ending = ending
    return scripted


@pytest_asyncio.fixture(loop_scope="function")
async def harness(
    peer: RoomPeer, adapter: ScriptedAdapter, shape: Shape
) -> AsyncIterator[Harness]:
    async with peer.client() as rest:
        room = wired_harness(peer, adapter, rest, shape)
        try:
            yield room
        finally:
            for task in adapter.detached:
                task.cancel()
            await room.runtime.stop()
