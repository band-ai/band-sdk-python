"""Refusal notifications retry only unaccepted work through the room scheduler."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from band.platform.link import BandLink
from tests.runtime.conftest import wait_for_condition
from tests.runtime.deferral.kit import (
    ReadinessAdapter,
    SettledRefusalAdapter,
    cancel_blocker,
    complete_blocker,
)
from tests.runtime.helpers import (
    AGENT_ID,
    LifecyclePlatform,
    no_periodic_wake_config,
    rest_client_over,
    run_through,
)
from tests.toolkit.timecontrol import controlled_clock


class TestRefusalReadiness:
    @pytest.mark.looptime
    async def test_already_complete_blocker_wakes_once_without_retry_spin(self) -> None:
        peer = LifecyclePlatform()
        blocker = asyncio.get_running_loop().create_future()
        blocker.set_result(None)
        adapter = SettledRefusalAdapter(blocker)
        async with rest_client_over(peer.answer) as rest, controlled_clock() as clock:
            link = BandLink(agent_id=AGENT_ID, api_key="test-key")
            link.rest = rest
            ctx = run_through(adapter, link, config=no_periodic_wake_config())
            await ctx.start()
            try:
                peer.add_message("A")
                await ctx.on_event(peer.event("A"))
                await clock.elapse(1)
                assert adapter.attempts == 2
                assert peer.marked("processed") == []
                assert peer.marked("failed") == []
            finally:
                await ctx.stop()

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
            await ctx.start()
            peer.add_message("A")
            await ctx.on_event(peer.event("A"))
            await adapter.refused.wait()
            await ctx.stop()
            replacement_blocker = asyncio.get_running_loop().create_future()
            replacement_adapter = ReadinessAdapter(replacement_blocker)
            replacement = run_through(
                replacement_adapter, link, config=no_periodic_wake_config()
            )
            await replacement.start()
            try:
                await replacement_adapter.refused.wait()
                blocker.set_result(None)
                await blocker
                assert ctx.scheduler.pending_resync is None
                assert adapter.completed == []
                assert replacement_adapter.completed == []
                replacement_blocker.set_result(None)
                await wait_for_condition(lambda: peer.marked("processed") == ["A"])
                assert replacement_adapter.completed == ["A"]
            finally:
                replacement_blocker.cancel()
                await replacement.stop()
