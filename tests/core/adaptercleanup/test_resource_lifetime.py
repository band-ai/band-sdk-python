"""Both real hosts release resources only after framework completion."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import BandConfigError
from tests.adaptercleanup import (
    HostKind,
    PassiveAdapter,
    TeardownAdapter,
    host_for,
    resource_peer,
)
from tests.adapters.hold import Hold


class TestResourceLifetime:
    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_fresh_host_adapter_and_resource_serve_after_disposal(
        self, kind: HostKind
    ) -> None:
        for _ in range(2):
            adapter = PassiveAdapter()
            async with resource_peer() as client, host_for(adapter, kind) as host:
                adapter.add_cleanup(client.aclose)
                await host.start()
                assert (await client.get("/resource")).status_code == 200
                await host.stop()
                assert client.is_closed

    @pytest.mark.parametrize("kind", list(HostKind))
    @pytest.mark.parametrize("framework", [False, True])
    async def test_inherited_and_no_super_teardown_release_real_resource(
        self, kind: HostKind, framework: bool
    ) -> None:
        adapter = TeardownAdapter() if framework else PassiveAdapter()
        async with resource_peer() as client, host_for(adapter, kind) as host:
            adapter.add_cleanup(client.aclose)
            await host.start()
            assert (await client.get("/resource")).json() == {"value": "open"}
            await adapter.cleanup_all()
            assert (await client.get("/resource")).status_code == 200
            await host.stop()
            with pytest.raises(RuntimeError, match="closed"):
                await client.get("/resource")
            with pytest.raises(BandConfigError, match="fresh"):
                await host.start()

    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_held_framework_teardown_keeps_resource_open(
        self, kind: HostKind
    ) -> None:
        adapter = TeardownAdapter()
        hold = adapter.cleanup_hold = Hold()
        async with resource_peer() as client, host_for(adapter, kind) as host:
            adapter.add_cleanup(client.aclose)
            await host.start()
            stopping = asyncio.create_task(host.stop())
            async with hold:
                assert (await client.get("/resource")).status_code == 200
                assert not stopping.done()
            await stopping
            assert client.is_closed

    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_legacy_callback_free_restart_still_works(
        self, kind: HostKind
    ) -> None:
        adapter = TeardownAdapter()
        async with host_for(adapter, kind) as host:
            await host.start()
            await host.stop()
            await host.start()
            await host.stop()
            assert adapter.cleanup_calls == 2
