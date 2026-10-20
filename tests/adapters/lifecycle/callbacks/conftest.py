"""Run resource lifetime guarantees on discovered providers and both hosts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from band.core.simple_adapter import SimpleAdapter
from tests.adaptercleanup import HostKind, host_for, resource_peer
from tests.adapters.lifecycle.callbacks.kit import CallbackHost
from tests.adapters.lifecycle.kit import hosted
from tests.adapters.lifecycle.profiles import registered_profiles

PROFILES = registered_profiles()


@pytest_asyncio.fixture(
    loop_scope="function",
    params=[(name, kind) for name in PROFILES for kind in HostKind],
    ids=lambda cell: f"{cell[0]}-{cell[1]}",
)
async def callback_host(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[CallbackHost]:
    name, kind = request.param
    async with hosted(PROFILES[name], tmp_path, monkeypatch, start=False) as provider:
        adapter = provider.adapter
        assert isinstance(adapter, SimpleAdapter)
        async with resource_peer() as client, host_for(adapter, kind) as host:
            lifetime = CallbackHost(provider, host, client)
            adapter.add_cleanup(lifetime.close_resource)
            await host.start()
            yield lifetime
