"""Interrupting an in-flight reasoning cycle."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from band.runtime.execution import ExecutionContext, TurnOutcome
from tests.conftest import BlockingHandler, make_message_event


class TestInterruptInFlightCycle:
    async def test_interrupt_cancels_cycle_marks_processed_loop_alive(self, mock_link):
        """Interrupt aborts the cycle, sends nothing, consumes the message, and
        the loop stays alive to process a fresh message afterward."""
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")

        event = make_message_event(msg_id="msg-1")
        proc = asyncio.create_task(ctx._process_event(event))
        await handler.started.wait()

        # Interrupt from the "receive task" side.
        assert ctx.interrupt() is True

        result = await proc
        assert result is TurnOutcome.ADVANCED  # loop continues, not a failure
        assert handler.cancelled.is_set()  # cycle was aborted
        assert "msg-1" not in handler.completed  # nothing was sent
        # Consumed: durable mark + local dedupe.
        mock_link.mark_processed.assert_awaited_once_with("room-123", "msg-1")
        assert "msg-1" in ctx.claims.completed_ids(ctx.room_id)
        assert ctx._active_cycle_task is None

        # Loop still alive: a fresh message processes normally.
        handler2 = BlockingHandler(block=False)
        ctx._on_execute = handler2
        result2 = await ctx._process_event(make_message_event(msg_id="msg-2"))
        assert result2 is TurnOutcome.ADVANCED
        assert handler2.completed == ["msg-2"]

    async def test_interrupt_during_tool_call_drops_result(self, mock_link):
        """A tool call already executing is abandoned (await dropped); its
        result is never sent."""
        in_tool = asyncio.Event()
        tool_results: list[str] = []

        async def fake_tool():
            await asyncio.sleep(60)
            return "tool-output"

        async def on_execute(ctx, event):
            in_tool.set()
            result = await fake_tool()
            tool_results.append(result)  # must never run

        ctx = ExecutionContext("room-123", mock_link, on_execute, agent_id="agent-123")
        proc = asyncio.create_task(ctx._process_event(make_message_event(msg_id="m")))
        await in_tool.wait()

        ctx.interrupt()
        await proc

        assert tool_results == []  # tool result abandoned, not delivered

    async def test_interrupt_between_cycles_is_noop(self, mock_link):
        """Interrupt with no active cycle is a clean no-op and must not set the
        flag (which would mis-flag the next cycle)."""
        ctx = ExecutionContext("room-123", mock_link, AsyncMock(), agent_id="agent-123")
        assert ctx.interrupt() is False

        # Next cycle runs normally.
        result = await ctx._process_event(make_message_event(msg_id="m1"))
        assert result is TurnOutcome.ADVANCED
        mock_link.mark_processed.assert_awaited_once_with("room-123", "m1")

    async def test_interrupted_cleanup_failure_is_logged(
        self, mock_link, caplog
    ) -> None:
        started = asyncio.Event()

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            started.set()
            try:
                await asyncio.Future[None]()
            except asyncio.CancelledError:
                raise RuntimeError("provider interrupt failed") from None

        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        with caplog.at_level(logging.ERROR, logger="band.runtime.execution"):
            turn = asyncio.create_task(ctx._process_event(make_message_event()))
            await started.wait()
            ctx.interrupt()
            assert await turn is TurnOutcome.ADVANCED

        [record] = caplog.records
        assert record.exc_info is not None
        assert str(record.exc_info[1]) == "provider interrupt failed"

    @pytest.mark.looptime
    async def test_cleanup_outliving_its_grace_is_reported(
        self, mock_link, caplog
    ) -> None:
        started, finish = asyncio.Event(), asyncio.Event()

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            started.set()
            try:
                await asyncio.Future[None]()
            except asyncio.CancelledError:
                await finish.wait()
                raise

        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        with caplog.at_level(logging.WARNING, logger="band.runtime.execution"):
            turn = asyncio.create_task(ctx._process_event(make_message_event()))
            await started.wait()
            ctx.interrupt()
            assert await turn is TurnOutcome.ADVANCED

        [record] = caplog.records
        assert record.levelno == logging.WARNING
        assert "outlived its cleanup grace" in record.getMessage()
        finish.set()
        assert await ctx.stop()
