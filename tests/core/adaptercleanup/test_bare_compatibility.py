"""Bare protocols keep optional teardown and restart without new state."""

from __future__ import annotations

import pytest

from tests.adaptercleanup import BareAdapter, BareTeardownAdapter, HostKind, host_for


class TestBareCompatibility:
    @pytest.mark.parametrize("kind", list(HostKind))
    @pytest.mark.parametrize("teardown", [False, True])
    async def test_protocol_implementation_needs_no_cleanup_state(
        self, kind: HostKind, teardown: bool
    ) -> None:
        adapter = BareTeardownAdapter() if teardown else BareAdapter()
        async with host_for(adapter, kind) as host:
            for _ in range(2):
                await host.start()
                await host.stop()
            assert adapter.starts == 2
            if isinstance(adapter, BareTeardownAdapter):
                assert adapter.stops == 2
