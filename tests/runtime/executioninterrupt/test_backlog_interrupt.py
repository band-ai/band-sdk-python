"""Interrupting backlog processing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from band.runtime.execution import ExecutionContext, ResyncOutcome, TurnOutcome
from band.runtime.types import SessionConfig
from tests.conftest import BlockingHandler
from tests.runtime.executioninterrupt.kit import (
    backlog_message,
)


class TestBacklogInterrupt:
    async def test_interrupt_during_backlog_consumes_and_advances(self, mock_link):
        """Interrupt during a /next backlog cycle consumes the message and the
        sync advances (does not retry the interrupted turn)."""
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        msg = backlog_message("bk1")
        proc = asyncio.create_task(ctx._process_backlog_message(msg))
        await handler.started.wait()

        ctx.interrupt()
        result = await proc

        assert result is TurnOutcome.ADVANCED
        mock_link.mark_processed.assert_awaited_once_with("room-123", "bk1")
        assert "bk1" in ctx.claims.completed_ids(ctx.room_id)

    async def test_stop_during_backlog_leaves_actionable(self, mock_link):
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        proc = asyncio.create_task(ctx._process_backlog_message(backlog_message("bk2")))
        await handler.started.wait()

        ctx.interrupt(kind="stop")
        result = await proc

        assert result is TurnOutcome.PAUSED
        mock_link.mark_processed.assert_not_awaited()
        assert "bk2" not in ctx.claims.completed_ids(ctx.room_id)

    async def test_stop_mid_resync_loop_does_not_reprocess(self, mock_link):
        """A stop landing mid-cycle during ``_resync_pending_messages`` must not
        be undone by the very next /next call in the same loop.

        The platform's /next excludes only 'processed' messages, so a
        'processing' message left behind by stop is returned again on the next
        poll. If the enclosing loop doesn't notice ``_stopped``, it will
        re-claim and fully run the very cycle stop just aborted -- silently
        breaking the "stop -> goes quiet until play" contract.
        """
        processed_ids: set[str] = set()

        async def fake_mark_processed(room_id, msg_id):
            processed_ids.add(msg_id)
            return True

        mock_link.mark_processed = AsyncMock(side_effect=fake_mark_processed)

        async def fake_get_next(room_id):
            # Mirrors the real /next contract: keep returning the message until
            # it's actually marked processed.
            if "loop1" in processed_ids:
                return None
            return backlog_message("loop1")

        mock_link.get_next_message = AsyncMock(side_effect=fake_get_next)

        handler = BlockingHandler()  # hangs until cancelled by stop_room() below
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            handler,
            agent_id="agent-123",
            # A high retry cap so the retry tracker can't itself block a second
            # attempt -- the test must fail (or pass) on the _stopped guard
            # alone, not be masked by the unrelated max-retries limit.
            config=SessionConfig(max_message_retries=10),
        )

        resync_task = asyncio.create_task(ctx._resync_pending_messages())
        await handler.started.wait()

        ctx.stop_room()
        result = await asyncio.wait_for(resync_task, timeout=5)

        assert result is ResyncOutcome.STOPPED
        # The adapter must not run a second time on the very next /next poll.
        assert handler.invoked == ["loop1"]
        mock_link.mark_processed.assert_not_awaited()
        assert "loop1" not in ctx.claims.completed_ids(ctx.room_id)
