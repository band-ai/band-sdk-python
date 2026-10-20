"""The outer host barrier protects inner and wrapper callback resources."""

from __future__ import annotations

import pytest

from band.agent import Agent
from band.core.exceptions import BandConfigError
from tests.adaptercleanup import HostKind, TeardownAdapter, host_for, resource_peer
from tests.core.agentlifecycle.kit import make_runtime
from tests.integrations.slack.wrappingkit import _make_adapter


class TestCallbackComposition:
    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_inner_callback_retry_precedes_outer_lifo_release(
        self, kind: HostKind
    ) -> None:
        inner = TeardownAdapter()
        wrapper, _, _, _ = _make_adapter(inner=inner)
        order: list[str] = []
        failing = True
        async with resource_peer() as resource, host_for(wrapper, kind) as host:

            async def inner_older() -> None:
                order.append("inner-older")
                if failing:
                    raise OSError("Inner resource release failed")
                assert (await resource.get("/resource")).status_code == 200

            async def inner_newer() -> None:
                order.append("inner-newer")
                assert (await resource.get("/resource")).status_code == 200

            async def outer_older() -> None:
                order.append("outer-older")
                await resource.aclose()

            async def outer_newer() -> None:
                order.append("outer-newer")
                assert (await resource.get("/resource")).status_code == 200

            inner.add_cleanup(inner_older)
            inner.add_cleanup(inner_newer)
            wrapper.add_cleanup(outer_older)
            wrapper.add_cleanup(outer_newer)
            await host.start()
            try:
                with pytest.raises(ExceptionGroup):
                    await host.stop()
                assert order == ["inner-newer", "inner-older"]
                assert (await resource.get("/resource")).status_code == 200
                assert inner.cleanup_calls == 1
            finally:
                failing = False
                await host.stop()
            expected = [
                "inner-newer",
                "inner-older",
                "inner-older",
                "outer-newer",
                "outer-older",
            ]
            assert order == expected
            assert resource.is_closed
            assert inner.cleanup_calls == 1
            await host.stop()
            assert order == expected

    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_inner_only_registration_inherits_ownership_and_disposed_reuse(
        self, kind: HostKind
    ) -> None:
        inner = TeardownAdapter()
        wrapper, _, _, _ = _make_adapter(inner=inner)
        async with resource_peer() as client, host_for(wrapper, kind) as host:
            inner.add_cleanup(client.aclose)
            await host.start()
            await host.stop()
            assert client.is_closed
            assert inner.cleanup_calls == 1
            with pytest.raises(BandConfigError, match="fresh"):
                await host.start()

    async def test_outer_failure_defers_both_stacks_and_skips_successful_inner(
        self,
    ) -> None:
        inner = TeardownAdapter()
        wrapper, _, _, _ = _make_adapter(inner=inner)
        runtime = make_runtime()
        runtime.stop.side_effect = [OSError("Runtime failed"), True]
        order: list[str] = []
        failing = True

        async def inner_close() -> None:
            order.append("inner")

        async def outer_close() -> None:
            order.append("outer")
            if failing:
                raise OSError("Outer resource failed")

        inner.add_cleanup(inner_close)
        wrapper.add_cleanup(outer_close)
        host = Agent(runtime=runtime, adapter=wrapper)
        await host.start()
        with pytest.raises(ExceptionGroup):
            await host.stop()
        assert order == []
        assert inner.cleanup_calls == 1
        with pytest.raises(ExceptionGroup):
            await host.stop()
        assert order == ["inner", "outer"]
        failing = False
        await host.stop()
        assert order == ["inner", "outer", "outer"]
        assert inner.cleanup_calls == 1

    async def test_foreign_inner_rejection_leaves_wrapper_unclaimed(self) -> None:
        inner = TeardownAdapter()
        wrapper, _, _, _ = _make_adapter(inner=inner)

        async def close() -> None:
            return None

        inner.add_cleanup(close)
        first = Agent(runtime=make_runtime(), adapter=inner)
        await first.start()
        second_runtime = make_runtime()
        second = Agent(runtime=second_runtime, adapter=wrapper)
        with pytest.raises(BandConfigError, match="another host"):
            await second.start()
        second_runtime.claim_single_instance.assert_not_called()
        assert wrapper._disposal.owner is None
        await first.stop()

    async def test_metadata_failure_releases_inner_callbacks_without_unstarted_teardown(
        self,
    ) -> None:
        inner = TeardownAdapter()
        wrapper, _, _, _ = _make_adapter(inner=inner)
        runtime = make_runtime()
        runtime.initialize.side_effect = LookupError("Metadata failed")
        async with resource_peer() as client:
            inner.add_cleanup(client.aclose)
            with pytest.raises(LookupError, match="Metadata failed"):
                await Agent(runtime=runtime, adapter=wrapper).start()
            assert client.is_closed
            assert inner.cleanup_calls == 0
