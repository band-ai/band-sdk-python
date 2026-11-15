"""Shutdown priority over stop and cancellation-resistant cycles."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.core.turn_lifecycle import CYCLE_CANCEL_GRACE_SECONDS, bind_turn_task
from band.runtime.cycle import TurnScope
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.adapters.hold import Hold
from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.executioninterrupt.kit import ClosingExecution
from tests.runtime.executioninterrupt.shutdown import (
    FinalizingHandler,
    ObservedTurnTask,
    managed_execution,
)
from tests.runtime.helpers import AGENT_ID, ROOM_ID
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock


class TestRoomShutdown:
    """Shutdown wins over an observed stop and waits out stubborn cycles."""

    async def test_shutdown_priority_over_observed_stop(
        self, mock_link: MagicMock
    ) -> None:
        handler = FinalizingHandler()
        ctx = ExecutionContext(ROOM_ID, mock_link, handler)
        async with managed_execution(ctx, release=handler.finalization.released):
            await ctx.start()
            await ctx.on_event(make_message_event(msg_id="shutdown"))
            await handler.started.wait()
            scope = ctx.current_scope
            assert scope is not None
            ctx.observe_platform_stop(scope)
            await handler.finalization.arrived()
            async with background(ctx.stop()) as stopping:
                handler.finalization.released.set()
                await stopping
            assert not ctx.is_running
            mock_link.mark_processed.assert_not_awaited()

    async def test_shutdown_exits_when_stopped_provider_suppresses_cancellation(
        self, mock_link: MagicMock
    ) -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def handler(ctx: ExecutionContext, event: object) -> None:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                return

        ctx = ExecutionContext(ROOM_ID, mock_link, handler)
        async with managed_execution(ctx, release=release):
            await ctx.start()
            await ctx.on_event(make_message_event(msg_id="shutdown-suppressed"))
            await started.wait()
            scope = ctx.current_scope
            assert scope is not None
            ctx.observe_platform_stop(scope)
            await ctx.stop()
            assert not ctx.is_running
            mock_link.mark_processed.assert_not_awaited()

    async def test_observer_shutdown_without_running_room_loop(
        self, mock_link: MagicMock
    ) -> None:
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def observer(ctx: ExecutionContext, scope: TurnScope) -> None:
            entered.set()
            try:
                await asyncio.Future[None]()
            finally:
                cancelled.set()

        ctx = ExecutionContext(
            ROOM_ID, mock_link, AsyncMock(), on_platform_stop=observer
        )
        async with managed_execution(ctx):
            scope = await ctx.begin_delivery()
            assert scope is not None
            ctx.observe_platform_stop(scope)
            await entered.wait()
            await ctx.stop()
            assert cancelled.is_set()

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_retried_stop_cancels_an_observer_that_swallowed_the_first(
        self, mock_link: MagicMock
    ) -> None:
        entered, cancelled, release = (asyncio.Event() for _ in range(3))

        async def observer(ctx: ExecutionContext, scope: TurnScope) -> None:
            task = asyncio.current_task()
            assert task is not None
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    if task.cancelling() == 2:
                        raise

        ctx = ExecutionContext(
            ROOM_ID, mock_link, AsyncMock(), on_platform_stop=observer
        )
        async with controlled_clock() as clock, managed_execution(ctx, release=release):
            scope = await ctx.begin_delivery()
            assert scope is not None
            ctx.observe_platform_stop(scope)
            await entered.wait()
            observing = scope.observer_task
            assert observing is not None
            async with background(ctx.stop()) as stopping:
                await cancelled.wait()
                await clock.elapse(CYCLE_CANCEL_GRACE_SECONDS)
                with pytest.raises(CleanupIncompleteError):
                    await stopping
            assert observing.cancelling() == 1

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_interrupted_drain_still_keeps_the_stubborn_task_for_stop(
        self, mock_link: MagicMock
    ) -> None:
        handler = FinalizingHandler()
        ctx = ExecutionContext(ROOM_ID, mock_link, AsyncMock())
        async with (
            controlled_clock() as clock,
            background(handler(ctx, object())) as work,
            managed_execution(ctx, release=handler.finalization.released),
        ):
            await handler.started.wait()
            owner = ObservedTurnTask(work)
            owner.terminate()
            await handler.cancellations.get()
            async with background(ctx._drain_or_retain(owner)) as draining:
                await owner.draining.wait()
                draining.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await draining
            owner.draining.clear()
            async with background(ctx.stop()) as stopping:
                await owner.draining.wait()
                await clock.elapse(CYCLE_CANCEL_GRACE_SECONDS)
                with pytest.raises(CleanupIncompleteError):
                    await stopping
            assert not work.done()

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_cancellation_resistant_cycle_keeps_stop_pending_until_settled(
        self, mock_link: MagicMock
    ) -> None:
        handler = FinalizingHandler()
        ctx = ClosingExecution(ROOM_ID, mock_link, handler)
        ctx.waiting_for_idle = asyncio.Event()
        async with (
            controlled_clock() as clock,
            managed_execution(ctx, release=handler.finalization.released),
        ):
            await ctx.start()
            await ctx.on_event(make_message_event(msg_id="stubborn"))
            await handler.started.wait()
            async with background(
                ctx.stop(timeout=CYCLE_CANCEL_GRACE_SECONDS)
            ) as stopping:
                await ctx.waiting_for_idle.wait()
                await clock.elapse(CYCLE_CANCEL_GRACE_SECONDS)
                await handler.cancellations.get()
                await clock.elapse(CYCLE_CANCEL_GRACE_SECONDS)
                with pytest.raises(CleanupIncompleteError):
                    await stopping
            async with background(
                ctx.stop(timeout=CYCLE_CANCEL_GRACE_SECONDS)
            ) as retry:
                await handler.cancellations.get()
                await clock.elapse(CYCLE_CANCEL_GRACE_SECONDS)
                with pytest.raises(CleanupIncompleteError):
                    await retry
            handler.finalization.released.set()
            await handler.finished.wait()
            assert await ctx.stop(timeout=CYCLE_CANCEL_GRACE_SECONDS) is False
            assert not ctx.is_running

    async def test_runtime_stop_leaves_a_released_worker_to_room_cleanup(
        self, mock_link: MagicMock
    ) -> None:
        processed = asyncio.Event()

        async def mark_processed(room_id: str, msg_id: str) -> bool:
            processed.set()
            return True

        mock_link.mark_processed.side_effect = mark_processed
        async with background(asyncio.Event().wait()) as worker:

            async def handler(ctx: ExecutionContext, event: object) -> None:
                bind_turn_task(AgentTools.from_context(ctx), worker)

            ctx = ExecutionContext(ROOM_ID, mock_link, handler, agent_id=AGENT_ID)
            async with managed_execution(ctx):
                await ctx.start()
                await ctx.on_event(make_message_event(msg_id="released"))
                await processed.wait()
                assert await ctx.stop()
                assert not worker.done()
        assert worker.cancelled()

    async def test_restart_gets_its_own_graceful_stop_result(
        self, mock_link: MagicMock
    ) -> None:
        handler = BlockingHandler()
        ctx = ExecutionContext(ROOM_ID, mock_link, handler, agent_id=AGENT_ID)
        async with managed_execution(ctx):
            await ctx.start()
            await ctx.on_event(make_message_event(msg_id="first"))
            await handler.started.wait()
            assert await ctx.stop(timeout=0) is False
            await ctx.start()
            assert await ctx.stop() is True

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_stop_finishes_a_slow_working_false_outside_settlement(
        self, mock_link: MagicMock
    ) -> None:
        handler = BlockingHandler()
        false_report = Hold()

        async def report_activity(
            room_id: str, working: bool, **kwargs: object
        ) -> bool:
            if not working:
                await false_report.wait()
            return True

        mock_link.report_activity = report_activity
        ctx = ExecutionContext(ROOM_ID, mock_link, handler)
        async with (
            controlled_clock() as clock,
            managed_execution(ctx, release=false_report.released),
        ):
            await ctx.start()
            await ctx.on_event(make_message_event(msg_id="working-false"))
            await handler.started.wait()
            async with background(ctx.stop()) as stopping:
                async with false_report:
                    await clock.elapse(CYCLE_CANCEL_GRACE_SECONDS)
                    assert not stopping.done()
                assert await stopping is True
