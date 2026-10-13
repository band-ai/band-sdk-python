"""Stopping and resuming a room."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from band.runtime.execution import BacklogProcessResult, ExecutionContext, ResyncOutcome
from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.executioninterrupt.kit import (
    backlog_message,
)


class TestStopRoomResumeRoom:
    async def test_stop_room_sets_flag_and_interrupts(self, mock_link):
        """stop_room aborts the in-flight cycle and sets the local _stopped flag."""
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        proc = asyncio.create_task(ctx._process_event(make_message_event(msg_id="x")))
        await handler.started.wait()

        ctx.stop_room()
        result = await proc

        assert ctx._stopped is True
        assert result is True
        mock_link.mark_processed.assert_not_awaited()

    async def test_stopped_room_skips_new_message(self, mock_link):
        """A WS trigger arriving while stopped is left actionable (never claimed
        or marked), not processed."""
        handler = BlockingHandler(block=False)
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        ctx._stopped = True

        result = await ctx._process_event(make_message_event(msg_id="while-stopped"))

        assert result is True
        assert handler.invoked == []  # adapter never invoked
        mock_link.mark_processing.assert_not_awaited()
        mock_link.mark_processed.assert_not_awaited()

    async def test_resume_room_clears_flag_and_requests_resync(self, mock_link):
        """play clears _stopped and enqueues a resync sentinel (the /next
        rehydration catch-up)."""
        ctx = ExecutionContext("room-123", mock_link, AsyncMock(), agent_id="agent-123")
        ctx._stopped = True

        await ctx.resume_room()

        assert ctx._stopped is False
        # A resync sentinel was enqueued (request_resync) for the loop to catch up.
        assert ctx.queue.qsize() == 1

    async def test_stopped_sync_does_not_use_processing_list(self, mock_link):
        """A stopped /next response does not fall back to an ungated list."""
        mock_link.get_stale_processing_messages = AsyncMock(
            return_value=[backlog_message("stuck-in-processing")]
        )
        handler = BlockingHandler(block=False)
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        ctx._stopped = True

        ok = await ctx._synchronize_with_next()

        assert ok is True
        assert handler.invoked == []  # adapter not invoked while stopped
        mock_link.get_stale_processing_messages.assert_not_awaited()

    async def test_resume_replays_and_responds(self, mock_link):
        """After play, the loop catches up via /next and the adapter runs for
        the replayed backlog message."""
        # Backlog has one message waiting (the one left actionable while stopped).
        replayed = backlog_message("replayed-1")
        calls = {"n": 0}

        async def get_next(room_id):
            calls["n"] += 1
            return replayed if calls["n"] == 1 else None

        mock_link.get_next_message = AsyncMock(side_effect=get_next)
        handler = BlockingHandler(block=False)
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        # Simulate: was stopped, now resuming.
        ctx._stopped = False
        ok = await ctx._resync_pending_messages()

        assert ok is ResyncOutcome.WORK
        assert handler.completed == ["replayed-1"]

    async def test_stop_does_not_poison_retry_budget(self, mock_link):
        """A cycle aborted by stop must not count against the message's retry
        budget — at the real default of one retry, the replayed backlog
        message (delivered again via /next after play) must still reach the
        handler instead of immediately landing in permanently_failed."""
        # First attempt hangs (gets stopped); the redelivered replay completes.
        handler = BlockingHandler(block=1)

        # Real default (SessionConfig().max_message_retries == 1) — no
        # generous override, so a poisoned attempt count would trip this.
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")

        proc = asyncio.create_task(ctx._process_event(make_message_event(msg_id="p1")))
        await handler.started.wait()
        ctx.stop_room()
        assert await proc is True
        mock_link.mark_processed.assert_not_awaited()

        # Resume, then the platform replays the still-"processing" message via
        # /next — same message id, now delivered through the backlog path.
        handler.started.clear()
        result = await ctx._process_backlog_message(backlog_message("p1"))

        assert result == BacklogProcessResult.ADVANCED
        assert handler.completed == ["p1"]  # handler actually ran this time
        assert not ctx._retry_tracker.is_permanently_failed("p1")
