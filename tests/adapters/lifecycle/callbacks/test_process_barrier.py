"""Every provider closes real children before the shared resource."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import BandConfigError
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.callbacks.kit import CallbackHost
from tests.adapters.roompeer import write_instruction


class TestProcessBarrier:
    async def test_callback_retry_does_not_replay_provider_teardown(
        self, callback_host: CallbackHost, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider, host, resource = (
            callback_host.provider,
            callback_host.host,
            callback_host.resource,
        )
        assert await provider.send("room", write_instruction("ready")) == ["ready"]
        original = provider.adapter.cleanup_all
        calls = 0

        async def cleanup() -> None:
            nonlocal calls
            calls += 1
            await original()

        monkeypatch.setattr(provider.adapter, "cleanup_all", cleanup)
        callback_host.fail_release = True
        with pytest.raises(ExceptionGroup):
            await host.stop()
        assert all(provider.exited)
        assert (await resource.get("/resource")).status_code == 200
        callback_host.fail_release = False
        await host.stop()
        assert calls == 1
        assert resource.is_closed

    async def test_held_child_close_keeps_resource_usable(
        self, callback_host: CallbackHost
    ) -> None:
        provider, host, resource = (
            callback_host.provider,
            callback_host.host,
            callback_host.resource,
        )
        assert await provider.send("room", write_instruction("ready")) == ["ready"]
        hold = provider.children[0].closing = Hold()
        stopping = asyncio.create_task(host.stop())
        async with hold:
            assert (await resource.get("/resource")).status_code == 200
            assert provider.exited == [False]
            assert not stopping.done()
        await stopping
        assert all(provider.exited)
        assert resource.is_closed
        with pytest.raises(BandConfigError, match="fresh"):
            await host.start()

    async def test_framework_reopen_preserves_registered_resource(
        self, callback_host: CallbackHost
    ) -> None:
        provider, host, resource = (
            callback_host.provider,
            callback_host.host,
            callback_host.resource,
        )
        assert await provider.send("room", write_instruction("before")) == ["before"]
        await provider.adapter.cleanup_all()
        assert (await resource.get("/resource")).status_code == 200
        await provider.adapter.on_started("Peer", "A subprocess under test")
        assert await provider.send("room", write_instruction("after")) == ["after"]
        assert (await resource.get("/resource")).status_code == 200
        await host.stop()
        assert all(provider.exited)
        assert resource.is_closed
