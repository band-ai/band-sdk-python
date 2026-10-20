from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.activity.kit import HeldLookup, HeldNativePeer


@pytest.fixture
async def held_lookup(baseline_settings: BaselineSettings) -> AsyncIterator[HeldLookup]:
    held = HeldLookup(baseline_settings.e2e_timeout)
    try:
        yield held
    finally:
        held.release.set()


@pytest.fixture
async def native_peer(
    baseline_settings: BaselineSettings,
) -> AsyncIterator[HeldNativePeer]:
    async with HeldNativePeer(baseline_settings.e2e_timeout).running() as peer:
        yield peer
