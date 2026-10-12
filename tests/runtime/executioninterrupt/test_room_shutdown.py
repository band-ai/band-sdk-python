"""Shutdown priority over stop and cancellation-resistant cycles."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.runtime.cycle import TurnScope
from band.runtime.execution import ExecutionContext
from tests.conftest import make_message_event


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

    async def test_cancellation_resistant_cycle_keeps_stop_pending_until_settled(
        self, mock_link, monkeypatch
    ) -> None:
        monkeypatch.setattr("band.runtime.execution.CYCLE_CANCEL_GRACE_SECONDS", 0.01)
        started, finish = asyncio.Event(), asyncio.Event()

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await finish.wait()
                raise

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

    async def test_stop_finishes_a_slow_working_false_outside_settlement(
        self, mock_link, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        grace = 0.05
        monkeypatch.setattr("band.runtime.execution.CYCLE_CANCEL_GRACE_SECONDS", grace)
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
