"""An ordinary runtime failure permits built-ins but defers resource release."""

from __future__ import annotations

import pytest

from band.agent import Agent
from tests.adaptercleanup import HostKind, TeardownAdapter, host_for, resource_peer


class TestRuntimeBarrier:
    async def test_successful_framework_teardown_is_not_replayed_after_runtime_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = TeardownAdapter()
        async with resource_peer() as client, host_for(adapter, HostKind.AGENT) as host:
            adapter.add_cleanup(client.aclose)
            await host.start()
            assert isinstance(host.owner, Agent)
            runtime = host.owner._runtime
            stop = runtime.stop
            first = True

            async def fail_once(timeout: float | None = None) -> bool:
                nonlocal first
                if first:
                    first = False
                    raise OSError("Runtime is still active")
                return await stop(timeout=timeout)

            monkeypatch.setattr(runtime, "stop", fail_once)
            with pytest.raises(ExceptionGroup, match="incomplete"):
                await host.stop()
            assert adapter.cleanup_calls == 1
            assert (await client.get("/resource")).status_code == 200
            await host.stop()
            assert adapter.cleanup_calls == 1
            assert client.is_closed
