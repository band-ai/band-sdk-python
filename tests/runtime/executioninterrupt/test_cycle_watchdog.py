"""The per-cycle watchdog deadline."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from band.runtime.execution import ExecutionContext, TurnOutcome
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.executioninterrupt.kit import (
    assert_fresh_cycle_still_propagates_shutdown_cancel,
)

_WATCHDOG_TEST_DEADLINE = 0.05
# Long enough that a handler blocking on it never finishes naturally within a test.
_NEVER_RETURNS_SECONDS = 60
# Large enough that the watchdog never fires; only used where the deadline
# itself must not be the thing under test.
_AMPLE_CYCLE_BUDGET_SECONDS = 5.0


class TestCycleWatchdog:
    """``max_cycle_seconds`` cancels a cycle from *inside* ExecutionContext when
    a handler never returns, unlike interrupt/stop which are external signals."""

    async def test_cycle_exceeding_max_cycle_seconds_is_cancelled_and_marked_failed(
        self, mock_link
    ):
        handler = BlockingHandler(block_seconds=_NEVER_RETURNS_SECONDS)
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            handler,
            agent_id="agent-123",
            config=SessionConfig(max_cycle_seconds=_WATCHDOG_TEST_DEADLINE),
        )

        result = await ctx._process_event(make_message_event(msg_id="watchdog-1"))

        # Loop continues; the watchdog is a handled error.
        assert result is TurnOutcome.ADVANCED
        assert handler.cancelled.is_set()  # the stuck cycle was actually cancelled
        mock_link.mark_processed.assert_not_awaited()
        mock_link.mark_failed.assert_awaited_once()
        room_id, msg_id, label = mock_link.mark_failed.await_args.args
        assert (room_id, msg_id) == ("room-123", "watchdog-1")
        assert (
            "max_cycle_seconds" in label
        )  # a diagnosable reason, not just "TimeoutError"

        # Loop stays alive: a fresh message still processes normally afterward.
        handler2 = BlockingHandler(block=False)
        ctx._on_execute = handler2
        result2 = await ctx._process_event(make_message_event(msg_id="watchdog-2"))
        assert result2 is TurnOutcome.ADVANCED
        assert handler2.completed == ["watchdog-2"]

    async def test_unset_max_cycle_seconds_never_cancels_a_slow_handler(
        self, mock_link
    ):
        """Default (unbounded) behavior is unchanged: no watchdog fires."""
        handler = BlockingHandler(block_seconds=0.05)
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")

        result = await ctx._process_event(make_message_event(msg_id="no-watchdog"))

        assert result is TurnOutcome.ADVANCED
        assert not handler.cancelled.is_set()
        mock_link.mark_processed.assert_awaited_once_with("room-123", "no-watchdog")
        mock_link.mark_failed.assert_not_awaited()

    async def test_handlers_own_timeout_error_is_not_mistaken_for_the_watchdog(
        self, mock_link
    ):
        """A handler's own bare TimeoutError, raised well inside the budget, must
        propagate as a normal handler failure -- not the watchdog's warning."""

        async def raises_own_timeout(ctx, event):
            raise TimeoutError("downstream call timed out")

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            raises_own_timeout,
            agent_id="agent-123",
            config=SessionConfig(max_cycle_seconds=_AMPLE_CYCLE_BUDGET_SECONDS),
        )

        result = await ctx._process_event(make_message_event(msg_id="own-timeout"))

        assert result is TurnOutcome.ADVANCED
        mock_link.mark_failed.assert_awaited_once()
        room_id, msg_id, label = mock_link.mark_failed.await_args.args
        assert (room_id, msg_id) == ("room-123", "own-timeout")
        assert label == "downstream call timed out"  # the handler's own message,
        # not the watchdog's -- nothing here actually exceeded the 5s budget.

    async def test_watchdog_cancellation_is_not_defeated_by_a_swallowed_cancel(
        self, mock_link
    ):
        """A handler that catches CancelledError and returns normally must still
        be reported as a watchdog failure, not a silent success."""

        async def swallows_cancellation(ctx, event):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return "handled it myself"

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            swallows_cancellation,
            agent_id="agent-123",
            config=SessionConfig(max_cycle_seconds=_WATCHDOG_TEST_DEADLINE),
        )

        result = await ctx._process_event(make_message_event(msg_id="swallowed"))

        # Loop continues; the watchdog is a handled error.
        assert result is TurnOutcome.ADVANCED
        mock_link.mark_processed.assert_not_awaited()
        mock_link.mark_failed.assert_awaited_once()
        room_id, msg_id, label = mock_link.mark_failed.await_args.args
        assert (room_id, msg_id) == ("room-123", "swallowed")
        assert "max_cycle_seconds" in label

    async def test_watchdog_expiry_honors_a_racing_interrupt_and_does_not_leak_it(
        self, mock_link
    ):
        """A concurrent interrupt()/stop() racing the watchdog's own deadline on
        the same task takes priority over the watchdog's own (coincidental)
        expiry -- honoring its documented contract instead of reporting the
        user's own interrupt as a timeout failure -- and must not leave a
        stale interrupt for a later, unrelated cycle's genuine
        shutdown cancellation to misread either way."""
        handler = BlockingHandler(block_seconds=_NEVER_RETURNS_SECONDS)
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            handler,
            agent_id="agent-123",
            config=SessionConfig(max_cycle_seconds=_WATCHDOG_TEST_DEADLINE),
        )

        # Simulate the race deterministically rather than chasing real timing:
        # an interrupt() landed on this same task right as the watchdog also
        # independently expired.
        async def interrupted_cleanup(ctx, event):
            try:
                await asyncio.Future[None]()
            finally:
                ctx.interrupt()
                await asyncio.sleep(0)

        ctx._on_execute = interrupted_cleanup
        result = await ctx._process_event(make_message_event(msg_id="race-1"))

        assert result is TurnOutcome.ADVANCED
        # The interrupt won, not the watchdog: consumed/acked, not failed.
        mock_link.mark_processed.assert_awaited_once_with("room-123", "race-1")
        mock_link.mark_failed.assert_not_awaited()

        await assert_fresh_cycle_still_propagates_shutdown_cancel(ctx, "race-2")

    async def test_child_completing_at_the_deadline_boundary_is_not_misreported(
        self, mock_link
    ):
        completed: list[str] = []

        async def finish_at_deadline(ctx, event):
            completed.append("reply")

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            finish_at_deadline,
            agent_id="agent-123",
            config=SessionConfig(max_cycle_seconds=1e-9),
        )
        assert (
            await ctx._process_event(make_message_event(msg_id="boundary"))
            is TurnOutcome.ADVANCED
        )
        assert completed == ["reply"]
        mock_link.mark_processed.assert_awaited_once_with("room-123", "boundary")
        mock_link.mark_failed.assert_not_awaited()

    @pytest.mark.looptime
    async def test_watchdog_does_not_wait_for_stuck_cancellation_cleanup(
        self, mock_link
    ):
        """A cycle whose cancellation cleanup wedges must not block the room."""
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        cleanup_finished = asyncio.Event()

        async def blocks_during_cleanup(ctx, event):
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await release_cleanup.wait()
                cleanup_finished.set()

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            blocks_during_cleanup,
            agent_id="agent-123",
            config=SessionConfig(max_cycle_seconds=_WATCHDOG_TEST_DEADLINE),
        )

        try:
            with patch("band.core.turn_lifecycle.CYCLE_CANCEL_GRACE_SECONDS", 0.01):
                result = await asyncio.wait_for(
                    ctx._process_event(make_message_event(msg_id="stuck-cleanup")),
                    timeout=0.2,
                )

            assert result is TurnOutcome.ADVANCED
            assert cleanup_started.is_set()
            assert not cleanup_finished.is_set()
            mock_link.mark_processed.assert_not_awaited()
            mock_link.mark_failed.assert_awaited_once()
            assert "max_cycle_seconds" in mock_link.mark_failed.await_args.args[2]
        finally:
            release_cleanup.set()
            await asyncio.wait_for(cleanup_finished.wait(), timeout=0.2)
