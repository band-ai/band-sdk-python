"""Refusal notifications retry only unaccepted work through the room scheduler."""

from __future__ import annotations

import asyncio
import gc
import weakref
from collections.abc import Callable
from contextlib import AsyncExitStack

import pytest

from band.platform.link import BandLink
from tests.runtime.conftest import Waiting, wait_for_condition
from tests.runtime.deferral.kit import (
    GatedRefusalAdapter,
    ReadinessAdapter,
    SettledRefusalAdapter,
    callbacks_finished,
    cancel_blocker,
    complete_blocker,
)
from tests.runtime.helpers import (
    AGENT_ID,
    LifecyclePlatform,
    no_periodic_wake_config,
    rest_client_over,
    run_through,
    running_execution,
)
from tests.toolkit.tasks import background


class TestRefusalReadiness:
    async def test_restart_observes_the_same_pending_blocker_again(self) -> None:
        peer = LifecyclePlatform()
        blocker = asyncio.get_running_loop().create_future()
        adapter = ReadinessAdapter(blocker)
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(adapter, link, config=no_periodic_wake_config())
            async with AsyncExitStack() as cleanup:
                cleanup.callback(blocker.cancel)
                cleanup.push_async_callback(ctx.stop)
                await ctx.start()
                peer.add_message("A")
                await ctx.on_event(peer.event("A"))
                await adapter.refused.wait()
                await Waiting.suspended(ctx)
                await ctx.stop()
                adapter.refused.clear()
                await ctx.start()
                await adapter.refused.wait()
                await Waiting.suspended(ctx)
                assert adapter.attempts == 2

                blocker.set_result(None)
                await wait_for_condition(lambda: peer.marked("processed") == ["A"])
                assert adapter.completed == ["A"]
                assert peer.marked("failed") == []

    async def test_restart_observes_a_completed_blocker_once_in_the_new_lifetime(
        self,
    ) -> None:
        peer = LifecyclePlatform()
        blocker = asyncio.get_running_loop().create_future()
        blocker.set_result(None)
        adapter = SettledRefusalAdapter(blocker)
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(adapter, link, config=no_periodic_wake_config())
            async with running_execution(ctx):
                peer.add_message("A")
                await ctx.on_event(peer.event("A"))
                await adapter.attempted.get()
                await adapter.attempted.get()
                await callbacks_finished()
                await Waiting.suspended(ctx)
                assert adapter.attempts == 2

                await ctx.stop()
                await ctx.start()
                await adapter.attempted.get()
                await callbacks_finished()
                await Waiting.suspended(ctx)
                assert adapter.attempts == 4
                assert peer.marked("processed") == []
                assert peer.marked("failed") == []

    async def test_already_complete_blocker_wakes_once_without_retry_spin(self) -> None:
        peer = LifecyclePlatform()
        blocker = asyncio.get_running_loop().create_future()
        blocker.set_result(None)
        adapter = SettledRefusalAdapter(blocker)
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(adapter, link, config=no_periodic_wake_config())
            async with running_execution(ctx):
                peer.add_message("A")
                await ctx.on_event(peer.event("A"))
                await adapter.attempted.get()
                await adapter.attempted.get()
                await callbacks_finished()
                await Waiting.suspended(ctx)
                assert adapter.attempts == 2
                await ctx.request_resync()
                await adapter.attempted.get()
                await callbacks_finished()
                await Waiting.suspended(ctx)
                assert adapter.attempts == 3
                assert peer.marked("processed") == []
                assert peer.marked("failed") == []

    @pytest.mark.parametrize("finish", [complete_blocker, cancel_blocker])
    async def test_a_settled_blocker_retries_the_unaccepted_input(
        self, finish: Callable[[asyncio.Future[None]], None]
    ) -> None:
        peer = LifecyclePlatform()
        blocker = asyncio.get_running_loop().create_future()
        adapter = ReadinessAdapter(blocker)
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(adapter, link, config=no_periodic_wake_config())
            await ctx.start()
            try:
                peer.add_message("A")
                await ctx.on_event(peer.event("A"))
                await adapter.refused.wait()
                finish(blocker)
                await wait_for_condition(lambda: peer.marked("processed") == ["A"])
                assert adapter.completed == ["A"]
                assert peer.marked("failed") == []
            finally:
                blocker.cancel()
                await ctx.stop()

    async def test_worker_completion_rechecks_the_remaining_backend_stop(self) -> None:
        peer = LifecyclePlatform()
        worker = asyncio.get_running_loop().create_future()
        stop = asyncio.get_running_loop().create_future()
        adapter = ReadinessAdapter(worker, stop)
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(adapter, link, config=no_periodic_wake_config())
            await ctx.start()
            try:
                peer.add_message("A")
                await ctx.on_event(peer.event("A"))
                await adapter.refused.wait()
                adapter.refused.clear()
                worker.set_result(None)
                await adapter.refused.wait()
                assert adapter.completed == []
                stop.set_result(None)
                await wait_for_condition(lambda: peer.marked("processed") == ["A"])
                assert adapter.completed == ["A"]
                assert peer.marked("failed") == []
            finally:
                worker.cancel()
                stop.cancel()
                await ctx.stop()

    async def test_closed_execution_does_not_resume_when_its_blocker_finishes(
        self,
    ) -> None:
        peer = LifecyclePlatform()
        blocker = asyncio.get_running_loop().create_future()
        adapter = ReadinessAdapter(blocker)
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(adapter, link, config=no_periodic_wake_config())
            async with AsyncExitStack() as cleanup:
                cleanup.callback(blocker.cancel)
                cleanup.push_async_callback(ctx.stop)
                await ctx.start()
                peer.add_message("A")
                await ctx.on_event(peer.event("A"))
                await adapter.refused.wait()
                await Waiting.suspended(ctx)
                await ctx.stop()
                replacement_blocker = asyncio.get_running_loop().create_future()
                replacement_adapter = ReadinessAdapter(replacement_blocker)
                replacement = run_through(
                    replacement_adapter, link, config=no_periodic_wake_config()
                )
                cleanup.callback(replacement_blocker.cancel)
                cleanup.push_async_callback(replacement.stop)
                await replacement.start()
                await replacement_adapter.refused.wait()
                blocker.set_result(None)
                await callbacks_finished()
                assert ctx.scheduler.pending_resync is None
                assert adapter.completed == []
                assert replacement_adapter.completed == []
                replacement_blocker.set_result(None)
                await wait_for_condition(lambda: peer.marked("processed") == ["A"])
                assert replacement_adapter.completed == ["A"]

    async def test_already_queued_notification_cannot_wake_a_closing_execution(
        self,
    ) -> None:
        peer = LifecyclePlatform()
        blocker = asyncio.get_running_loop().create_future()
        adapter = ReadinessAdapter(blocker)
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(adapter, link, config=no_periodic_wake_config())
            async with running_execution(ctx):
                peer.add_message("A")
                await ctx.on_event(peer.event("A"))
                await adapter.refused.wait()
                await Waiting.suspended(ctx)
                blocker.set_result(None)
                await ctx.stop()
                await callbacks_finished()
                assert ctx.scheduler.pending_resync is None
                assert adapter.completed == []

    async def test_refusal_during_graceful_drain_does_not_retain_the_execution(
        self,
    ) -> None:
        peer = LifecyclePlatform()
        blocker = asyncio.get_running_loop().create_future()
        adapter = GatedRefusalAdapter(blocker)
        async with rest_client_over(peer.answer) as rest:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(adapter, link, config=no_periodic_wake_config())
            try:
                async with AsyncExitStack() as cleanup:
                    await ctx.start()
                    cleanup.push_async_callback(ctx.stop)
                    cleanup.callback(adapter.release.set)
                    peer.add_message("A")
                    await ctx.on_event(peer.event("A"))
                    await adapter.entered.wait()
                    async with background(ctx.stop(timeout=30)) as stopping:
                        await callbacks_finished()
                        assert ctx.is_closing and not stopping.done()
                        adapter.release.set()
                        assert await stopping
                    # A second stop would remove a leaked observer and mask the bug.
                    cleanup.pop_all()
                retired = weakref.ref(ctx)
                del ctx
                gc.collect()
                assert not blocker.done()
                assert retired() is None
            finally:
                blocker.cancel()
