"""REST stop refusals that land after an explicit interrupt."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.client.streaming import ControlMode
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from band.runtime.types import SessionConfig
from tests.conftest import make_message_event
from tests.runtime.executioninterrupt.kit import (
    backlog_message,
)
from tests.runtime.helpers import (
    LifecyclePlatform,
    rest_client_over,
)


class TestLateRestRefusals:
    """A late stop refusal never overrides a newer explicit interrupt."""

    @pytest.mark.parametrize("suppress_cancel", [False, True])
    @pytest.mark.parametrize("max_cycle_seconds", [None, 1.0])
    async def test_newer_interrupt_consumes_a_scope_aborted_by_rest_stop(
        self, mock_link, suppress_cancel: bool, max_cycle_seconds: float | None
    ) -> None:
        cleanup_started = asyncio.Event()
        invoked: list[str] = []
        peer = LifecyclePlatform(stopped=True)
        async with rest_client_over(peer.answer) as rest:
            mock_link.rest = rest

            async def handler(ctx: ExecutionContext, event: Any) -> None:
                invoked.append(event.payload.id)
                try:
                    await AgentTools.from_context(ctx).send_event("thought", "thought")
                except asyncio.CancelledError:
                    cleanup_started.set()
                    try:
                        await asyncio.Future[None]()
                    except asyncio.CancelledError:
                        if not suppress_cancel:
                            raise

            ctx = ExecutionContext(
                "room-123",
                mock_link,
                handler,
                config=SessionConfig(max_cycle_seconds=max_cycle_seconds),
            )
            async with asyncio.TaskGroup() as tasks:
                processing = tasks.create_task(
                    ctx._process_event(make_message_event(msg_id="interrupted"))
                )
                await cleanup_started.wait()
                peer.stopped = False
                await ctx.resume_room()
                assert ctx.interrupt(kind=ControlMode.INTERRUPT)
                await processing

            assert ctx.claims.is_completed(ctx.room_id, "interrupted")
            mock_link.mark_processed.assert_awaited_once_with(
                ctx.room_id, "interrupted"
            )

            mock_link.mark_failed.assert_not_awaited()
            await ctx._process_backlog_message(backlog_message("interrupted"))
            assert invoked == ["interrupted"]

    async def test_late_rest_refusal_preserves_a_pending_explicit_interrupt(
        self,
        mock_link,
    ) -> None:
        entered = asyncio.Event()
        peer = LifecyclePlatform(stopped=True)
        async with rest_client_over(peer.answer) as rest:
            mock_link.rest = rest

            async def handler(ctx: ExecutionContext, event: Any) -> None:
                tools = AgentTools.from_context(ctx)
                entered.set()
                try:
                    await asyncio.Future[None]()
                except asyncio.CancelledError:
                    await tools.send_event("late", "thought")

            ctx = ExecutionContext("room-123", mock_link, handler)
            async with asyncio.TaskGroup() as tasks:
                processing = tasks.create_task(
                    ctx._process_event(make_message_event(msg_id="interrupted"))
                )
                await entered.wait()
                assert ctx.interrupt(kind=ControlMode.INTERRUPT)
                await processing

            assert ctx.claims.is_completed(ctx.room_id, "interrupted")
            mock_link.mark_processed.assert_awaited_once_with(
                ctx.room_id, "interrupted"
            )
