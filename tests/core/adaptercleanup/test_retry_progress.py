"""Cleanup retries keep only failed units, preserving original errors."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import BandConfigError
from tests.adaptercleanup import HostKind, TeardownAdapter, host_for, resource_peer
from tests.adapters.hold import Hold


class TestRetryProgress:
    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_independent_failures_continue_and_successes_do_not_replay(
        self, kind: HostKind
    ) -> None:
        adapter = TeardownAdapter()
        attempts: list[str] = []
        failure = OSError("Resource release failed")
        failing = True

        async def older() -> None:
            attempts.append("older")

        async def newer() -> None:
            attempts.append("newer")
            if failing:
                raise failure

        async with host_for(adapter, kind) as host:
            adapter.add_cleanup(older)
            adapter.add_cleanup(newer)
            await host.start()
            with pytest.raises(ExceptionGroup):
                await host.stop()
            assert attempts == ["newer", "older"]
            failing = False
            await host.stop()
            assert attempts == ["newer", "older", "newer"]
            assert adapter.cleanup_calls == 1
            assert len(failure.__notes__) == 1

    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_framework_failure_defers_callbacks(self, kind: HostKind) -> None:
        adapter = TeardownAdapter()
        async with resource_peer() as client, host_for(adapter, kind) as host:
            adapter.add_cleanup(client.aclose)
            await host.start()
            adapter.fail_cleanup = True
            with pytest.raises(ExceptionGroup if kind == HostKind.AGENT else OSError):
                await host.stop()
            assert (await client.get("/resource")).status_code == 200
            adapter.fail_cleanup = False
            await host.stop()
            assert client.is_closed

    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_caller_cancellation_preserves_each_hosts_service(
        self, kind: HostKind
    ) -> None:
        adapter = TeardownAdapter()
        callback = Hold()
        attempts = 0

        async def close() -> None:
            nonlocal attempts
            attempts += 1
            await callback.wait()

        async with host_for(adapter, kind) as host:
            adapter.add_cleanup(close)
            await host.start()
            stopping = asyncio.create_task(host.stop())
            await callback.arrived()
            stopping.cancel()
            with pytest.raises(asyncio.CancelledError):
                await stopping
            callback.released.set()
            await host.stop()
            assert attempts == (2 if kind == HostKind.AGENT else 1)
            assert adapter.cleanup_calls == 1

    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_recursive_shutdown_fails_instead_of_deadlocking(
        self, kind: HostKind
    ) -> None:
        adapter = TeardownAdapter()
        async with host_for(adapter, kind) as host:

            async def close() -> None:
                with pytest.raises(BandConfigError, match="outside"):
                    await host.stop()

            adapter.add_cleanup(close)
            await host.start()
            await host.stop()
