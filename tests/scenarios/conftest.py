"""Running agents on a local platform, each torn down after its scenario."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from tests.core.retention.kit import ResidenceHost
from tests.scenarios.oneshot import OneShotRig, oneshot_rig
from tests.scenarios.world import BandWorld, band_world


@pytest.fixture(autouse=True)
def pinned_periodic_wake(periodic_wake_after_the_full_interval: None) -> None:
    """A catch-up scenario must not pass through an early periodic resync."""


# Function-scoped loops: the socket server and client must share the test's
# loop, which the repo-wide session default would not give them.


@pytest_asyncio.fixture(loop_scope="function")
async def world() -> AsyncIterator[BandWorld]:
    async with band_world() as world:
        yield world


@pytest_asyncio.fixture(loop_scope="function")
async def world_with_bare_room() -> AsyncIterator[BandWorld]:
    """``bare`` is served by a custom execution without control methods."""
    async with band_world(bare_rooms=frozenset({"bare"})) as world:
        yield world


@pytest_asyncio.fixture(loop_scope="function")
async def oneshot() -> AsyncIterator[OneShotRig]:
    async with oneshot_rig() as rig:
        yield rig


@pytest_asyncio.fixture(loop_scope="function")
async def residences() -> AsyncIterator[ResidenceHost]:
    host = ResidenceHost()
    try:
        yield host
    finally:
        await host.close()
