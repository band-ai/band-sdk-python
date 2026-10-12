"""OneShotInvoker: startup."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.core.exceptions import (
    CleanupIncompleteError,
)
from band.core.model_catalog import ModelSelection, ModelSelectionError
from band.core.simple_adapter import SimpleAdapter
from band.core.types import Capability
from band.runtime.capabilities import FeatureFlag
from band.runtime.oneshot import (
    OneShotInvoker,
)
from tests.catalogs import CatalogAdapter
from tests.runtime.conftest import make_link_mock
from tests.runtime.oneshot.kit import make_adapter_mock, make_invoker, msg_body


class FilesAdapter(SimpleAdapter):
    """Bare SimpleAdapter declaring only Capability.FILES support."""

    SUPPORTED_CAPABILITIES = frozenset({Capability.FILES})

    async def on_message(self, *args: Any, **kwargs: Any) -> None:
        pass


class TestStartup:
    async def test_fetches_metadata_and_primes_adapter(self) -> None:
        link = make_link_mock(agent_name="Weather", agent_description="forecasts")
        adapter = make_adapter_mock()
        invoker = OneShotInvoker(link=link, adapter=adapter, agent_id="agent-1")

        await invoker.startup()

        assert invoker.agent_name == "Weather"
        assert invoker.agent_description == "forecasts"
        # Adapter primed with identity + metadata.
        assert adapter.platform.agent_id == "agent-1"
        adapter.on_started.assert_awaited_once_with("Weather", "forecasts")

    async def test_prunes_files_capability_when_flag_off(self) -> None:
        link = make_link_mock(feature_flags={FeatureFlag.FILE_TRANSFER: False})
        adapter = FilesAdapter(capabilities=Capability.FILES)
        invoker = OneShotInvoker(link=link, adapter=adapter, agent_id="agent-1")

        await invoker.startup()

        assert Capability.FILES not in adapter.features.capabilities

    async def test_keeps_files_capability_when_flag_on(self) -> None:
        link = make_link_mock(feature_flags={FeatureFlag.FILE_TRANSFER: True})
        adapter = FilesAdapter(capabilities=Capability.FILES)
        invoker = OneShotInvoker(link=link, adapter=adapter, agent_id="agent-1")

        await invoker.startup()

        assert Capability.FILES in adapter.features.capabilities

    async def test_startup_rejects_a_model_the_harness_does_not_offer(self) -> None:
        adapter = CatalogAdapter(ModelSelection(model="gpt-9"))
        invoker = OneShotInvoker(link=make_link_mock(), adapter=adapter, agent_id="a")

        with pytest.raises(ModelSelectionError, match='model "gpt-9"'):
            await invoker.startup()

        assert adapter.cleaned_up

    async def test_startup_is_idempotent(self) -> None:
        link = make_link_mock()
        adapter = make_adapter_mock()
        invoker = OneShotInvoker(link=link, adapter=adapter, agent_id="agent-1")

        await invoker.startup()
        await invoker.startup()

        link.rest.agent_api_identity.get_agent_me.assert_awaited_once()
        adapter.on_started.assert_awaited_once()

    async def test_handle_event_before_startup_raises(self) -> None:
        link = make_link_mock()
        invoker = OneShotInvoker(
            link=link, adapter=make_adapter_mock(), agent_id="agent-1"
        )
        with pytest.raises(RuntimeError, match="startup"):
            await invoker.handle_event(msg_body())

    async def test_shutdown_disposes_adapter_and_disconnects_link(self) -> None:
        link = make_link_mock()
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)
        await invoker.shutdown()
        adapter.cleanup_all.assert_awaited_once()
        link.disconnect.assert_awaited_once()

    async def test_failed_shutdown_disconnects_and_retries_cleanup(self) -> None:
        link = make_link_mock()
        adapter = make_adapter_mock()
        adapter.cleanup_all.side_effect = RuntimeError("teardown boom")
        invoker = await make_invoker(link, adapter)

        with pytest.raises(RuntimeError, match="teardown boom"):
            await invoker.shutdown()
        link.disconnect.assert_awaited_once()
        with pytest.raises(CleanupIncompleteError):
            await invoker.startup()

        adapter.cleanup_all.side_effect = None
        await invoker.shutdown()
        assert adapter.cleanup_all.await_count == 2
        await invoker.shutdown()
        assert adapter.cleanup_all.await_count == 2
        await invoker.startup()
        assert adapter.on_started.await_count == 2

    async def test_concurrent_shutdowns_join_one_cleanup(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def held_cleanup() -> None:
            entered.set()
            await release.wait()

        link = make_link_mock()
        adapter = make_adapter_mock()
        adapter.cleanup_all.side_effect = held_cleanup
        invoker = await make_invoker(link, adapter)

        first = asyncio.create_task(invoker.shutdown())
        await entered.wait()
        second = asyncio.create_task(invoker.shutdown())
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        adapter.cleanup_all.assert_awaited_once()
        link.disconnect.assert_awaited_once()

    async def test_cancelled_shutdown_waiter_keeps_cleanup_owned(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def held_cleanup() -> None:
            entered.set()
            await release.wait()

        link = make_link_mock()
        adapter = make_adapter_mock()
        adapter.cleanup_all.side_effect = held_cleanup
        invoker = await make_invoker(link, adapter)

        waiter = asyncio.create_task(invoker.shutdown())
        await entered.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        with pytest.raises(CleanupIncompleteError):
            await invoker.startup()

        retry = asyncio.create_task(invoker.shutdown())
        await asyncio.sleep(0)
        release.set()
        await retry
        adapter.cleanup_all.assert_awaited_once()
        await invoker.startup()
        assert adapter.on_started.await_count == 2
