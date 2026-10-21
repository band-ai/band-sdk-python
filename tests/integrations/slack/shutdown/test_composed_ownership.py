"""Wrapper registrations exclusively claim every explicitly owned inner lifetime."""

from __future__ import annotations

import pytest

from band.core.exceptions import BandConfigError
from tests.adaptercleanup import HostKind, TeardownAdapter, host_for, resource_peer
from tests.integrations.slack.wrappingkit import _make_adapter


class TestComposedOwnership:
    @pytest.mark.parametrize("kind", list(HostKind))
    @pytest.mark.parametrize("nested", [False, True], ids=["wrapper", "nested-wrapper"])
    async def test_wrapper_only_resources_protect_inner_from_foreign_host(
        self, kind: HostKind, nested: bool
    ) -> None:
        inner = TeardownAdapter()
        wrapper, _, _, _ = _make_adapter(inner=inner)
        if nested:
            wrapper, _, _, _ = _make_adapter(inner=wrapper)
        async with (
            resource_peer() as resource,
            host_for(wrapper, kind) as first,
            host_for(inner, kind) as second,
        ):
            wrapper.add_cleanup(resource.aclose)
            await first.start()
            platform = inner.platform
            with pytest.raises(BandConfigError, match="another host"):
                await second.start()
            assert inner.platform is platform
            assert (await resource.get("/resource")).status_code == 200
            await first.stop()
            assert resource.is_closed
            with pytest.raises(BandConfigError, match="fresh"):
                await second.start()
            assert inner.cleanup_calls == 1

    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_foreign_legacy_inner_refusal_leaves_wrapper_usable_after_release(
        self, kind: HostKind
    ) -> None:
        inner = TeardownAdapter()
        wrapper, _, _, _ = _make_adapter(inner=inner)
        async with (
            resource_peer() as resource,
            host_for(inner, kind) as first,
            host_for(wrapper, kind) as second,
        ):
            wrapper.add_cleanup(resource.aclose)
            await first.start()
            with pytest.raises(BandConfigError, match="another host"):
                await second.start()
            assert (await resource.get("/resource")).status_code == 200
            await first.stop()
            await second.start()
            assert (await resource.get("/resource")).status_code == 200
            await second.stop()
            assert resource.is_closed

    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_nested_foreign_refusal_preserves_every_unaccepted_wrapper(
        self, kind: HostKind
    ) -> None:
        inner = TeardownAdapter()
        middle, _, _, _ = _make_adapter(inner=inner)
        outer, _, _, _ = _make_adapter(inner=middle)
        releases: list[str] = []

        async def release_outer() -> None:
            releases.append("outer")

        async def release_middle() -> None:
            releases.append("middle")

        outer.add_cleanup(release_outer)
        async with (
            resource_peer() as resource,
            host_for(inner, kind) as first,
            host_for(outer, kind) as second,
        ):
            await first.start()
            platform = inner.platform
            with pytest.raises(BandConfigError, match="another host"):
                await second.start()
            assert inner.platform is platform
            middle.add_cleanup(release_middle)
            outer.add_cleanup(resource.aclose)
            assert (await resource.get("/resource")).status_code == 200
            assert releases == []
            await first.stop()
            await second.start()
            assert (await resource.get("/resource")).status_code == 200
            await second.stop()
            assert releases == ["middle", "outer"]
            assert resource.is_closed
            assert inner.cleanup_calls == 2
