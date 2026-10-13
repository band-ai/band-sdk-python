"""Shutdown priority over stop and cancellation-resistant cycles."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.core.turn_lifecycle import TurnTask, bind_turn_task
from band.runtime.cycle import TurnScope
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.helpers import resist_cancellation_until


class TestRoomShutdown:
    """Shutdown wins over an observed stop and waits out stubborn cycles."""

    async def test_shutdown_priority_over_observed_stop(self, mock_link) -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
                raise

        ctx = ExecutionContext("room-123", mock_link, handler)
        await ctx.start()
        await ctx.on_event(make_message_event(msg_id="shutdown"))
        await started.wait()
        ctx.observe_platform_stop(ctx.current_scope)
        stop = asyncio.create_task(ctx.stop())
        release.set()
        await stop
        assert not ctx.is_running
        mock_link.mark_processed.assert_not_awaited()

    async def test_shutdown_exits_when_stopped_provider_suppresses_cancellation(
        self,
        mock_link,
    ) -> None:
        started, released = asyncio.Event(), asyncio.Event()

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            started.set()
            try:
                await released.wait()
            except asyncio.CancelledError:
                return

        ctx = ExecutionContext("room-123", mock_link, handler)
        await ctx.start()
        await ctx.on_event(make_message_event(msg_id="shutdown-suppressed"))
        await started.wait()
        ctx.observe_platform_stop(ctx.current_scope)
        await ctx.stop()
        assert not ctx.is_running
        mock_link.mark_processed.assert_not_awaited()

    async def test_observer_shutdown_without_running_room_loop(self, mock_link) -> None:
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def observer(ctx: ExecutionContext, scope: TurnScope) -> None:
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        ctx = ExecutionContext(
            "room-123", mock_link, AsyncMock(), on_platform_stop=observer
        )
        await ctx._begin_scope()
        ctx.observe_platform_stop(ctx.current_scope)
        await entered.wait()
        await ctx.stop()
        assert cancelled.is_set()

    async def test_retried_stop_cancels_an_observer_that_swallowed_the_first(
        self, mock_link, monkeypatch
    ) -> None:
        monkeypatch.setattr("band.core.turn_lifecycle.CYCLE_CANCEL_GRACE_SECONDS", 0.01)
        entered, cancels = asyncio.Event(), 0

        async def observer(ctx: ExecutionContext, scope: TurnScope) -> None:
            nonlocal cancels
            entered.set()
            while True:
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    cancels += 1
                    if cancels == 2:
                        raise

        ctx = ExecutionContext(
            "room-123", mock_link, AsyncMock(), on_platform_stop=observer
        )
        await ctx._begin_scope()
        ctx.observe_platform_stop(ctx.current_scope)
        await entered.wait()
        with pytest.raises(CleanupIncompleteError):
            await ctx.stop()
        await ctx.stop()
        assert cancels == 2

    async def test_interrupted_drain_still_keeps_the_stubborn_task_for_stop(
        self, mock_link
    ) -> None:
        finish = asyncio.Event()
        stubborn = TurnTask(asyncio.create_task(resist_cancellation_until(finish)))
        await asyncio.sleep(0)
        stubborn.terminate()
        ctx = ExecutionContext("room-123", mock_link, AsyncMock())
        draining = asyncio.create_task(ctx._drain_or_retain(stubborn))
        await asyncio.sleep(0)
        draining.cancel()
        with pytest.raises(asyncio.CancelledError):
            await draining

        try:
            with pytest.raises(CleanupIncompleteError):
                await ctx.stop()
        finally:
            finish.set()
        await ctx.stop()

    async def test_cancellation_resistant_cycle_keeps_stop_pending_until_settled(
        self, mock_link, monkeypatch
    ) -> None:
        monkeypatch.setattr("band.core.turn_lifecycle.CYCLE_CANCEL_GRACE_SECONDS", 0.01)
        started, finish = asyncio.Event(), asyncio.Event()

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            started.set()
            await resist_cancellation_until(finish)

        ctx = ExecutionContext("room-123", mock_link, handler)
        await ctx.start()
        await ctx.on_event(make_message_event(msg_id="stubborn"))
        await started.wait()
        with pytest.raises(CleanupIncompleteError):
            await ctx.stop(timeout=0.01)
        with pytest.raises(CleanupIncompleteError):
            await ctx.stop(timeout=60)
        finish.set()
        assert await ctx.stop(timeout=60) is False
        assert not ctx.is_running

    async def test_runtime_stop_leaves_a_released_worker_to_room_cleanup(
        self, mock_link
    ) -> None:
        released: list[asyncio.Task[None]] = []
        processed = asyncio.Event()
        mock_link.mark_processed.side_effect = lambda *_: processed.set() or True

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            worker = asyncio.create_task(asyncio.Event().wait())
            bind_turn_task(AgentTools.from_context(ctx), worker)
            released.append(worker)

        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        await ctx.start()
        await ctx.on_event(make_message_event(msg_id="released"))
        await processed.wait()

        assert await ctx.stop(timeout=10)
        assert not released[0].done()
        released[0].cancel()

    async def test_restart_gets_its_own_graceful_stop_result(self, mock_link) -> None:
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        await ctx.start()
        await ctx.on_event(make_message_event(msg_id="first"))
        await handler.started.wait()
        assert await ctx.stop(timeout=0) is False
        await ctx.start()
        assert await ctx.stop() is True

    async def test_stop_finishes_a_slow_working_false_outside_settlement(
        self, mock_link, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        grace = 0.05
        monkeypatch.setattr(
            "band.core.turn_lifecycle.CYCLE_CANCEL_GRACE_SECONDS", grace
        )
        started, false_sent = asyncio.Event(), asyncio.Event()

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            started.set()
            await asyncio.Event().wait()

        async def report_activity(
            room_id: str, working: bool, **kwargs: object
        ) -> bool:
            if not working:
                await asyncio.sleep(grace * 4)
                false_sent.set()
            return True

        mock_link.report_activity = report_activity
        ctx = ExecutionContext("room-123", mock_link, handler)
        await ctx.start()
        await ctx.on_event(make_message_event(msg_id="working-false"))
        await started.wait()
        assert await ctx.stop() is True
        assert false_sent.is_set()
