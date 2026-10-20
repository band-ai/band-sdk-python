"""Cleanup retries keep only failed units, preserving original errors."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import BandConfigError
from tests.adaptercleanup import HostKind, TeardownAdapter, host_for, resource_peer
from tests.adapters.hold import Hold


class TestRetryProgress:
    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_multiple_failures_preserve_independent_resource_progress(
        self, kind: HostKind
    ) -> None:
        adapter = TeardownAdapter()
        oldest_failure = OSError("Oldest resource release failed")
        newest_failure = LookupError("Newest resource release failed")
        attempts: list[str] = []
        failing = True
        async with (
            resource_peer() as oldest_resource,
            resource_peer() as middle_resource,
            resource_peer() as newest_resource,
            host_for(adapter, kind) as host,
        ):

            async def oldest() -> None:
                attempts.append("oldest")
                if failing:
                    raise oldest_failure
                await oldest_resource.aclose()

            async def middle() -> None:
                attempts.append("middle")
                await middle_resource.aclose()

            async def newest() -> None:
                attempts.append("newest")
                if failing:
                    raise newest_failure
                await newest_resource.aclose()

            adapter.add_cleanup(oldest)
            adapter.add_cleanup(middle)
            adapter.add_cleanup(newest)
            await host.start()
            try:
                with pytest.raises(ExceptionGroup) as raised:
                    await host.stop()
                assert (
                    raised.value.subgroup(lambda exc: exc is oldest_failure) is not None
                )
                assert (
                    raised.value.subgroup(lambda exc: exc is newest_failure) is not None
                )
                assert attempts == ["newest", "middle", "oldest"]
                assert middle_resource.is_closed
                assert (await oldest_resource.get("/resource")).status_code == 200
                assert (await newest_resource.get("/resource")).status_code == 200
                oldest_notes = list(oldest_failure.__notes__)
                newest_notes = list(newest_failure.__notes__)
                assert oldest_notes[0].startswith("Adapter cleanup registration 1:")
                assert newest_notes[0].startswith("Adapter cleanup registration 3:")
                assert adapter.cleanup_calls == 1
            finally:
                failing = False
                await host.stop()
            assert attempts == ["newest", "middle", "oldest", "newest", "oldest"]
            assert oldest_resource.is_closed
            assert newest_resource.is_closed
            assert adapter.cleanup_calls == 1
            assert oldest_failure.__notes__ == oldest_notes
            assert newest_failure.__notes__ == newest_notes

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

        async def middle() -> None:
            attempts.append("middle")
            if failing:
                raise failure

        async with host_for(adapter, kind) as host:
            adapter.add_cleanup(older)
            adapter.add_cleanup(middle)
            adapter.add_cleanup(newer)
            await host.start()
            with pytest.raises(ExceptionGroup):
                await host.stop()
            assert attempts == ["newer", "middle", "older"]
            note = failure.__notes__[0]
            assert note.startswith("Adapter cleanup registration 2:")
            with pytest.raises(ExceptionGroup):
                await host.stop()
            assert failure.__notes__ == [note]
            failing = False
            await host.stop()
            assert attempts == ["newer", "middle", "older", "middle", "middle"]
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
        attempts: list[str] = []

        async with resource_peer() as client, host_for(adapter, kind) as host:

            async def completed() -> None:
                attempts.append("completed")
                assert (await client.get("/resource")).status_code == 200

            async def close() -> None:
                attempts.append("held")
                await callback.wait()
                await client.aclose()

            adapter.add_cleanup(close)
            adapter.add_cleanup(completed)
            await host.start()
            stopping = asyncio.create_task(host.stop())
            try:
                await callback.arrived()
                stopping.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await stopping
                assert attempts == ["completed", "held"]
                assert (await client.get("/resource")).status_code == 200
            finally:
                callback.released.set()
                await asyncio.gather(stopping, return_exceptions=True)
                await host.stop()
            expected = ["completed", "held"]
            if kind == HostKind.AGENT:
                expected.append("held")
            assert attempts == expected
            assert adapter.cleanup_calls == 1
            assert client.is_closed

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
