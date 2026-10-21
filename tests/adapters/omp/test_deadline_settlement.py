from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.testing import FakeAgentTools, reported_failures
from tests.adapters.omp.kit import (
    Launches,
    SelfRetiringResolver,
    SettlingResolver,
    UncancellingResolver,
    cross_deadline,
    launches,
    run_turn,
    settling_adapter,
    start_cleanup,
    timeout_reports,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock

__all__ = ["launches"]


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestDeadlineSettlement:
    @pytest.mark.asyncio
    async def test_cleanup_during_a_deadline_settlement_keeps_the_timeout_report(
        self, launches: Launches, tmp_path: Path
    ):
        async with controlled_clock() as deadline_clock:
            resolver = SettlingResolver()
            adapter, releases = settling_adapter(
                tmp_path, resolver, turn_timeout_s=0.05
            )
            tools = FakeAgentTools()
            first = asyncio.create_task(run_turn(adapter, tools))
            await asyncio.wait_for(resolver.entered.wait(), 1)
            deadline_clock.advance(0.1)
            await asyncio.wait_for(resolver.cancel_seen.wait(), 1)  # the deadline fired

            cleanup = await start_cleanup(adapter)

            assert not cleanup.done()
            resolver.settle.set()
            await asyncio.wait_for(cleanup, 1)
            with pytest.raises(TimeoutError):
                await first
            assert resolver.cancellations == 1 and releases and all(releases)
            assert [
                (failure["provider"], failure.get("code"))
                for failure in reported_failures(tools)
            ] == [("omp", "timeout")]
            assert launches.runtimes == []

    @pytest.mark.asyncio
    async def test_cleanup_before_the_deadline_owns_the_turn_through_slow_settlement(
        self, launches: Launches, tmp_path: Path
    ):
        async with controlled_clock() as deadline_clock:
            resolver = SettlingResolver()
            adapter, releases = settling_adapter(tmp_path, resolver, turn_timeout_s=0.2)
            tools = FakeAgentTools()
            first = asyncio.create_task(run_turn(adapter, tools))
            await asyncio.wait_for(resolver.entered.wait(), 1)
            cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
            await asyncio.wait_for(resolver.cancel_seen.wait(), 1)

            # Virtual time crosses the original deadline while settlement is held.
            await cross_deadline(deadline_clock, 0.4)

            assert resolver.cancellations == 1 and not cleanup.done()
            assert "room-123" in adapter._workspaces.rooms
            resolver.settle.set()
            await asyncio.wait_for(cleanup, 1)
            with pytest.raises(asyncio.CancelledError):
                await first
            assert resolver.cancellations == 1 and releases and all(releases)
            assert not reported_failures(tools) and launches.runtimes == []
            await run_turn(adapter, FakeAgentTools())  # explicit recovery
            assert len(launches.runtimes) == 1
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_self_cleanup_after_a_caller_cancellation_disarms_the_deadline(
        self, launches: Launches, tmp_path: Path
    ):
        async with controlled_clock() as deadline_clock:
            resolver = SelfRetiringResolver()
            adapter, releases = settling_adapter(tmp_path, resolver, turn_timeout_s=0.2)
            resolver.adapter = adapter
            tools = FakeAgentTools()
            first = asyncio.create_task(run_turn(adapter, tools))
            await asyncio.wait_for(resolver.entered.wait(), 1)
            first.cancel()  # the caller cancels; the resolver then retires its own room
            await asyncio.wait_for(resolver.cancel_seen.wait(), 1)
            shutdown = await start_cleanup(adapter, shutdown=True)

            # Virtual time crosses the original deadline while settlement is held.
            await cross_deadline(deadline_clock, 0.4)

            assert (
                resolver.cancellations == 1 and not first.done() and not shutdown.done()
            )
            assert "room-123" in adapter._workspaces.rooms
            resolver.settle.set()
            await asyncio.wait_for(shutdown, 1)
            with pytest.raises(asyncio.CancelledError):
                await first
            assert resolver.cancellations == 1 and releases and all(releases)
            assert not reported_failures(tools) and launches.runtimes == []
            await run_turn(adapter, FakeAgentTools())  # explicit recovery
            assert len(launches.runtimes) == 1
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_self_cleanup_after_the_deadline_keeps_the_timeout_report(
        self, launches: Launches, tmp_path: Path
    ):
        async with controlled_clock() as deadline_clock:
            resolver = SelfRetiringResolver()
            adapter, releases = settling_adapter(
                tmp_path, resolver, turn_timeout_s=0.05
            )
            resolver.adapter = adapter
            tools = FakeAgentTools()
            first = asyncio.create_task(run_turn(adapter, tools))
            await asyncio.wait_for(resolver.entered.wait(), 1)
            deadline_clock.advance(0.1)
            await asyncio.wait_for(
                resolver.cancel_seen.wait(), 1
            )  # the deadline fired first

            resolver.settle.set()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(first, 1)
            await asyncio.wait_for(adapter.cleanup_all(), 1)

            assert resolver.cancellations == 1 and resolver.settled
            assert releases and all(releases)
            assert [
                (failure["provider"], failure.get("code"))
                for failure in reported_failures(tools)
            ] == [("omp", "timeout")]
            assert launches.runtimes == []

    @pytest.mark.asyncio
    async def test_cleanup_joins_a_fired_deadline_whose_cancellation_was_consumed(
        self, launches: Launches, tmp_path: Path
    ):
        async with controlled_clock() as deadline_clock:
            resolver = UncancellingResolver(returns=False)
            adapter, releases = settling_adapter(
                tmp_path, resolver, turn_timeout_s=0.05
            )
            tools = FakeAgentTools()
            first = asyncio.create_task(run_turn(adapter, tools))
            await asyncio.wait_for(resolver.entered.wait(), 1)
            deadline_clock.advance(0.1)
            await asyncio.wait_for(resolver.cancel_seen.wait(), 1)  # the deadline fired

            cleanup = await start_cleanup(adapter)

            assert not cleanup.done()
            assert (
                resolver.cancellations == 1 and "room-123" in adapter._workspaces.rooms
            )
            resolver.settle.set()
            await asyncio.wait_for(cleanup, 1)
            with pytest.raises(TimeoutError):
                await first
            assert resolver.cancellations == 1 and releases and all(releases)
            assert timeout_reports(tools) == [("omp", "timeout")]
            assert launches.runtimes == []

    @pytest.mark.asyncio
    async def test_a_result_returned_after_the_deadline_fired_launches_nothing(
        self, launches: Launches, tmp_path: Path
    ):
        async with controlled_clock() as deadline_clock:
            resolver = UncancellingResolver(returns=True)
            resolver.settle.set()  # settlement finishes and the resolver returns a server
            adapter, releases = settling_adapter(
                tmp_path, resolver, turn_timeout_s=0.05
            )
            tools = FakeAgentTools()
            turn = asyncio.create_task(run_turn(adapter, tools))
            await asyncio.wait_for(resolver.entered.wait(), 1)
            deadline_clock.advance(0.1)

            with pytest.raises(TimeoutError):
                await asyncio.wait_for(turn, 1)

            assert resolver.cancellations == 1 and resolver.settled
            assert launches.runtimes == [] and launches.plugins == []
            assert timeout_reports(tools) == [("omp", "timeout")]
            assert not adapter._workspaces.rooms and releases and all(releases)
            await run_turn(adapter, FakeAgentTools())  # explicit recovery
            assert len(launches.runtimes) == 1
            await adapter.cleanup_all()
