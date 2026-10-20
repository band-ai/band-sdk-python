"""Only an accepted resource lifetime prevents later wrapper adoption."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from band.agent import Agent
from band.core.exceptions import AgentAlreadyRunningError, BandConfigError
from tests.adaptercleanup import (
    Host,
    HostKind,
    TeardownAdapter,
    host_for,
    resource_peer,
)
from tests.adapters.hold import Hold
from tests.integrations.slack.wrappingkit import _make_adapter


class TestStartupAdoption:
    @pytest.mark.parametrize("kind", list(HostKind))
    @pytest.mark.parametrize("nested", [False, True], ids=["wrapper", "nested-wrapper"])
    async def test_failed_metadata_leaves_inner_available_but_registration_sealed(
        self, kind: HostKind, nested: bool, metadata_hold: Callable[[Host, bool], Hold]
    ) -> None:
        inner = TeardownAdapter()
        wrapper, _, _, _ = _make_adapter(inner=inner)
        if nested:
            wrapper, _, _, _ = _make_adapter(inner=wrapper)
        async with (
            resource_peer() as resource,
            host_for(inner, kind) as failed,
            host_for(wrapper, kind) as replacement,
        ):
            hold = metadata_hold(failed, True)
            hold.released.set()
            with pytest.raises(LookupError, match="Metadata failed"):
                await failed.start()
            with pytest.raises(BandConfigError, match="before starting"):
                inner.add_cleanup(resource.aclose)
            wrapper.add_cleanup(resource.aclose)
            await replacement.start()
            assert (await resource.get("/resource")).status_code == 200
            await replacement.stop()
            assert resource.is_closed
            assert inner.cleanup_calls == 1

    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_wrapper_claim_during_metadata_refuses_late_start_without_mutation(
        self, kind: HostKind, metadata_hold: Callable[[Host, bool], Hold]
    ) -> None:
        inner = TeardownAdapter()
        wrapper, _, _, _ = _make_adapter(inner=inner)
        async with (
            resource_peer() as resource,
            host_for(inner, kind) as delayed,
            host_for(wrapper, kind) as replacement,
        ):
            hold = metadata_hold(delayed, False)
            starting = asyncio.create_task(delayed.start())
            async with hold:
                with pytest.raises(BandConfigError, match="before starting"):
                    inner.add_cleanup(resource.aclose)
                wrapper.add_cleanup(resource.aclose)
                await replacement.start()
                platform = inner.platform
            with pytest.raises(BandConfigError, match="another host"):
                await starting
            assert inner.platform is platform
            assert inner.cleanup_calls == 0
            assert (await resource.get("/resource")).status_code == 200
            await replacement.stop()
            assert resource.is_closed
            assert inner.cleanup_calls == 1

    async def test_identity_refusal_leaves_inner_available_for_resource_wrapper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inner = TeardownAdapter()
        wrapper, _, _, _ = _make_adapter(inner=inner)
        async with (
            resource_peer() as resource,
            host_for(inner, HostKind.AGENT) as refused,
            host_for(wrapper, HostKind.AGENT) as replacement,
        ):
            assert isinstance(refused.owner, Agent)

            def refuse() -> None:
                raise AgentAlreadyRunningError("Identity is busy")

            monkeypatch.setattr(refused.owner.runtime, "claim_single_instance", refuse)
            with pytest.raises(AgentAlreadyRunningError, match="Identity is busy"):
                await refused.start()
            wrapper.add_cleanup(resource.aclose)
            await replacement.start()
            assert (await resource.get("/resource")).status_code == 200
            await replacement.stop()
            assert resource.is_closed

    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_disposed_wrapper_refuses_metadata_delayed_legacy_start(
        self, kind: HostKind, metadata_hold: Callable[[Host, bool], Hold]
    ) -> None:
        inner = TeardownAdapter()
        wrapper, _, _, _ = _make_adapter(inner=inner)
        async with (
            resource_peer() as resource,
            resource_peer() as external,
            host_for(inner, kind) as delayed,
            host_for(wrapper, kind) as replacement,
        ):
            hold = metadata_hold(delayed, False)
            starting = asyncio.create_task(delayed.start())
            async with hold:
                wrapper.add_cleanup(resource.aclose)
                await replacement.start()
                platform = inner.platform
                await replacement.stop()
                assert resource.is_closed
            with pytest.raises(BandConfigError, match="fresh"):
                await starting
            await delayed.stop()
            assert inner.platform is platform
            assert inner.cleanup_calls == 1
            assert resource.is_closed
            assert (await external.get("/resource")).status_code == 200
