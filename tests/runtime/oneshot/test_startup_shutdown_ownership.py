"""OneShotInvoker: shutdown never overlaps a held startup."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.runtime.oneshot import (
    OneShotInvoker,
)
from tests.runtime.conftest import make_link_mock
from tests.runtime.oneshot.kit import HeldCall, make_adapter_mock, msg_body


class TestStartupShutdownOwnership:
    """Shutdown keeps a held startup from becoming ready and never overlaps it."""

    async def test_shutdown_during_held_start_waits_then_cleans_up(self) -> None:
        order: list[str] = []
        link = make_link_mock()
        adapter = make_adapter_mock()
        on_started = HeldCall("on_started", order)
        adapter.on_started.side_effect = on_started.call
        adapter.cleanup_all.side_effect = lambda: order.append("cleanup")
        invoker = OneShotInvoker(link=link, adapter=adapter, agent_id="agent-1")

        starting = asyncio.create_task(invoker.startup())
        await on_started.entered.wait()
        stopping = asyncio.create_task(invoker.shutdown())
        await asyncio.sleep(0)
        assert "cleanup" not in order

        on_started.release.set()
        with pytest.raises(RuntimeError, match="shut down during startup"):
            await starting
        await stopping
        assert order == ["on_started started", "on_started finished", "cleanup"]
        with pytest.raises(RuntimeError, match="startup"):
            await invoker.handle_event(msg_body())

        adapter.on_started.side_effect = None
        await invoker.startup()
        await invoker.handle_event(msg_body())

    async def test_shutdown_during_metadata_fetch_never_starts_the_adapter(
        self,
    ) -> None:
        order: list[str] = []
        link = make_link_mock()
        identity = link.rest.agent_api_identity.get_agent_me
        metadata = HeldCall("metadata", order, result=identity.return_value)
        identity.side_effect = metadata.call
        adapter = make_adapter_mock()
        invoker = OneShotInvoker(link=link, adapter=adapter, agent_id="agent-1")

        starting = asyncio.create_task(invoker.startup())
        await metadata.entered.wait()
        stopping = asyncio.create_task(invoker.shutdown())
        await asyncio.sleep(0)
        metadata.release.set()
        with pytest.raises(RuntimeError, match="shut down during startup"):
            await starting
        await stopping
        adapter.on_started.assert_not_awaited()
        adapter.cleanup_all.assert_not_awaited()

    async def test_concurrent_startups_join_one_start(self) -> None:
        order: list[str] = []
        adapter = make_adapter_mock()
        on_started = HeldCall("on_started", order)
        adapter.on_started.side_effect = on_started.call
        invoker = OneShotInvoker(
            link=make_link_mock(), adapter=adapter, agent_id="agent-1"
        )

        first = asyncio.create_task(invoker.startup())
        await on_started.entered.wait()
        second = asyncio.create_task(invoker.startup())
        await asyncio.sleep(0)
        on_started.release.set()
        await asyncio.gather(first, second)
        adapter.on_started.assert_awaited_once()

    async def test_shutdown_waits_for_failed_start_cleanup_and_keeps_its_error(
        self,
    ) -> None:
        order: list[str] = []
        adapter = make_adapter_mock()
        adapter.on_started.side_effect = LookupError("boot failed")
        release = HeldCall("release", order)
        adapter.cleanup_all.side_effect = release.call
        invoker = OneShotInvoker(
            link=make_link_mock(), adapter=adapter, agent_id="agent-1"
        )

        starting = asyncio.create_task(invoker.startup())
        await release.entered.wait()
        stopping = asyncio.create_task(invoker.shutdown())
        await asyncio.sleep(0)
        release.release.set()
        with pytest.raises(LookupError, match="boot failed"):
            await starting
        await stopping
        assert order == ["release started", "release finished"]

    async def test_failed_start_whose_cleanup_fails_stays_pending(self) -> None:
        adapter = make_adapter_mock()
        adapter.on_started.side_effect = LookupError("boot failed")
        adapter.cleanup_all.side_effect = RuntimeError("cleanup failed")
        invoker = OneShotInvoker(
            link=make_link_mock(), adapter=adapter, agent_id="agent-1"
        )

        with pytest.raises(LookupError, match="boot failed"):
            await invoker.startup()
        with pytest.raises(CleanupIncompleteError, match="cleanup is incomplete"):
            await invoker.startup()

        adapter.on_started.side_effect = None
        adapter.cleanup_all.side_effect = None
        await invoker.shutdown()
        await invoker.startup()
