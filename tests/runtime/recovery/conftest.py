"""Fixtures: the real runtime wired to a stateful room peer."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from tests.runtime.recovery.kit import (
    Ending,
    Harness,
    RoomPeer,
    ScriptedAdapter,
    Shape,
    wired_harness,
)


@pytest.fixture(params=[Shape.OFF])
def shape(request: pytest.FixtureRequest) -> Shape:
    return request.param


@pytest.fixture
def ending() -> Ending:
    return Ending.DECLINES


@pytest.fixture
def room_peer() -> RoomPeer:
    return RoomPeer()


@pytest.fixture
def adapter(ending: Ending) -> ScriptedAdapter:
    scripted = ScriptedAdapter()
    scripted.ending = ending
    return scripted


@pytest_asyncio.fixture(loop_scope="function")
async def harness(
    room_peer: RoomPeer, adapter: ScriptedAdapter, shape: Shape
) -> AsyncIterator[Harness]:
    async with room_peer.client() as rest:
        room = wired_harness(peer=room_peer, adapter=adapter, rest=rest, shape=shape)
        try:
            yield room
        finally:
            for task in adapter.detached:
                task.cancel()
            await room.runtime.stop()
