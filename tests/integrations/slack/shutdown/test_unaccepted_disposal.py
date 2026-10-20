"""Internal resource disposal requires the host's ownership acceptance."""

from __future__ import annotations

import pytest

from band.core.exceptions import BandConfigError
from band.runtime.startup import dispose_adapter
from tests.adaptercleanup import HostKind, TeardownAdapter, host_for, resource_peer
from tests.integrations.slack.wrappingkit import _make_adapter


class TestUnacceptedDisposal:
    @pytest.mark.parametrize("kind", list(HostKind))
    @pytest.mark.parametrize(
        "inner_registration", [False, True], ids=["outer", "inner"]
    )
    async def test_refusal_keeps_resources_and_framework_usable_by_host(
        self, kind: HostKind, inner_registration: bool
    ) -> None:
        inner = TeardownAdapter()
        wrapper, _, _, _ = _make_adapter(inner=inner)
        async with resource_peer() as resource, host_for(wrapper, kind) as host:
            owner = inner if inner_registration else wrapper
            owner.add_cleanup(resource.aclose)
            with pytest.raises(BandConfigError, match="start.*host"):
                await dispose_adapter(wrapper)
            assert inner.cleanup_calls == 0
            assert (await resource.get("/resource")).status_code == 200
            await host.start()
            await host.stop()
            assert resource.is_closed
            assert inner.cleanup_calls == 1
