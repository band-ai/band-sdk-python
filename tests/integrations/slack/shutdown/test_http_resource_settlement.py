"""Mounted HTTP ingress settles before host-owned resources are released."""

from __future__ import annotations

import asyncio

import pytest

from band.agent import Agent
from tests.adaptercleanup import HostKind, host_for, resource_peer
from tests.adapters.waiting import until
from tests.integrations.slack.shutdown.kit import GatedBrain
from tests.integrations.slack.wrappingkit import (
    _make_adapter,
    _mention_event,
    _post_slack_event,
)


class TestHTTPResourceSettlement:
    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_metadata_failure_settles_already_mounted_ingress(
        self, kind: HostKind, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with resource_peer() as resource:
            brain = GatedBrain(resource)
            brain.add_cleanup(resource.aclose)
            wrapper, _, _, _ = _make_adapter(inner=brain)
            async with host_for(wrapper, kind) as host:
                entered, fail = asyncio.Event(), asyncio.Event()
                original = LookupError("Metadata unavailable")

                async def metadata() -> None:
                    entered.set()
                    await fail.wait()
                    raise original

                if isinstance(host.owner, Agent):
                    monkeypatch.setattr(host.owner.runtime, "initialize", metadata)
                else:
                    monkeypatch.setattr(host.owner, "_fetch_agent_metadata", metadata)
                starting = asyncio.create_task(host.start())
                try:
                    await entered.wait()
                    response = await _post_slack_event(
                        wrapper, wrapper.config.apps[0], _mention_event()
                    )
                    assert response.status_code == 200
                    await brain.entered.wait()
                    fail.set()
                    await until(
                        lambda: starting.done() or brain.finalizing.is_set(),
                        failure="Failed startup never reached Slack settlement",
                    )
                    assert not starting.done()
                    assert (await resource.get("/resource")).status_code == 200
                    brain.release.set()
                    with pytest.raises(LookupError) as raised:
                        await starting
                    assert raised.value is original
                    assert brain.finished.is_set()
                    assert brain.cleanup_calls == 0
                    assert resource.is_closed
                    brain.entered.clear()
                    await _post_slack_event(
                        wrapper, wrapper.config.apps[0], _mention_event(ts="2")
                    )
                    await wrapper.wait_idle()
                    assert not brain.entered.is_set()
                finally:
                    fail.set()
                    brain.release.set()
                    await asyncio.gather(starting, return_exceptions=True)
                    await wrapper.cleanup_all()

    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_normal_shutdown_keeps_resource_for_http_finalizer(
        self, kind: HostKind
    ) -> None:
        async with resource_peer() as resource:
            brain = GatedBrain(resource)
            brain.add_cleanup(resource.aclose)
            wrapper, _, _, _ = _make_adapter(inner=brain)
            async with host_for(wrapper, kind) as host:
                await host.start()
                await _post_slack_event(
                    wrapper, wrapper.config.apps[0], _mention_event()
                )
                await brain.entered.wait()
                stopping = asyncio.create_task(host.stop())
                try:
                    await brain.finalizing.wait()
                    assert not stopping.done()
                    assert (await resource.get("/resource")).status_code == 200
                finally:
                    brain.release.set()
                    await stopping
                assert brain.finished.is_set()
                assert resource.is_closed
                assert brain.cleanup_calls == 1
