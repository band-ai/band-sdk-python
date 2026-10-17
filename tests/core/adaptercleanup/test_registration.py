"""Registration is explicit, per instance and sealed by host acceptance."""

from __future__ import annotations

import functools
from collections.abc import Awaitable

import pytest

from band.core.exceptions import BandConfigError
from tests.adaptercleanup import HostKind, PassiveAdapter, host_for, resource_peer


class TestRegistration:
    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_duplicate_callback_registrations_each_run(
        self, kind: HostKind
    ) -> None:
        adapter = PassiveAdapter()
        attempts = 0
        async with resource_peer() as resource, host_for(adapter, kind) as host:

            async def close() -> None:
                nonlocal attempts
                attempts += 1
                await resource.aclose()

            adapter.add_cleanup(close)
            adapter.add_cleanup(close)
            await host.start()
            assert (await resource.get("/resource")).status_code == 200
            await host.stop()
            assert resource.is_closed
            assert attempts == 2
            await host.stop()
            assert attempts == 2

    def test_noncallable_is_rejected_without_invocation(self) -> None:
        with pytest.raises(TypeError, match="client.aclose"):
            PassiveAdapter().add_cleanup(None)  # type: ignore[arg-type]

    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_ordinary_callable_returning_awaitable_and_partial(
        self, kind: HostKind
    ) -> None:
        closed: list[str] = []

        async def close(name: str) -> None:
            closed.append(name)

        adapter = PassiveAdapter()
        adapter.add_cleanup(functools.partial(close, "partial"))
        adapter.add_cleanup(lambda: close("factory"))
        assert not closed
        async with host_for(adapter, kind) as host:
            await host.start()
            with pytest.raises(BandConfigError, match="before starting"):
                adapter.add_cleanup(lambda: close("late"))
            await host.stop()
        assert closed == ["factory", "partial"]

    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_late_registration_is_refused_without_earlier_callbacks(
        self, kind: HostKind
    ) -> None:
        adapter = PassiveAdapter()
        async with host_for(adapter, kind) as host:
            await host.start()
            with pytest.raises(BandConfigError, match="before starting"):
                adapter.add_cleanup(host.stop)

    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_nonawaitable_failure_stays_pending_for_retry(
        self, kind: HostKind
    ) -> None:
        adapter = PassiveAdapter()
        valid = False
        completed: list[bool] = []

        async def close() -> None:
            completed.append(True)

        def callback() -> Awaitable[None] | None:
            return close() if valid else None

        adapter.add_cleanup(callback)  # type: ignore[arg-type]
        async with host_for(adapter, kind) as host:
            await host.start()
            with pytest.raises(ExceptionGroup) as failure:
                await host.stop()
            errors = failure.value.subgroup(TypeError)
            assert errors is not None
            valid = True
            await host.stop()
        assert completed == [True]
