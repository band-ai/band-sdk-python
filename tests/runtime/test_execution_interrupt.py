"""Tests for ExecutionContext per-cycle interrupt/stop.

The reasoning cycle runs as a cancellable child task so a control signal can
abort just one turn without killing the room's process loop. These tests drive
``_process_event`` directly with a controllable ``on_execute`` so we can cancel
mid-cycle deterministically.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from band.client.rest import AsyncRestClient
from band.client.streaming import ControlMode, DeliveryStatus
from band.core.exceptions import RoomExecutionStoppedError
from band.platform.link import BandLink
from band.runtime.cycle import TurnScope
from band.runtime.execution import BacklogProcessResult, ExecutionContext, ResyncOutcome
from band.runtime.tools.agent import AgentTools
from band.runtime.types import PlatformMessage, SessionConfig
from tests.conftest import BlockingHandler, make_message_event
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.control import AuxiliaryClaimRuntime
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    ClaimGate,
    LifecyclePlatform,
    rest_client_over,
)


@pytest.fixture
def mock_link():
    link = MagicMock()
    link.agent_id = "agent-123"
    link.rest = MagicMock()
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.mark_processing = AsyncMock(return_value=True)
    link.mark_processed = AsyncMock(return_value=True)
    link.mark_failed = AsyncMock(return_value=True)
    link.get_next_message = AsyncMock(return_value=None)
    link.get_stale_processing_messages = AsyncMock(return_value=[])
    link.report_activity = AsyncMock(return_value=True)
    return link


async def _assert_fresh_cycle_still_propagates_shutdown_cancel(
    ctx: ExecutionContext, msg_id: str
) -> None:
    """A cycle genuinely cancelled by shutdown (no new interrupt()) must
    propagate CancelledError, not get misclassified as an interrupt/stop via
    a leaked ``_interrupt_kind`` from whatever ran on ``ctx`` before it."""
    started = asyncio.Event()

    async def block(ctx, event):
        started.set()
        await asyncio.Event().wait()

    ctx._on_execute = block
    shutdown = asyncio.create_task(
        ctx._run_cycle(make_message_event(msg_id=msg_id), msg_id)
    )
    await started.wait()
    shutdown.cancel()

    with pytest.raises(asyncio.CancelledError):
        await shutdown


def _backlog_message(msg_id: str = "msg-bk") -> PlatformMessage:
    return PlatformMessage(
        id=msg_id,
        room_id="room-123",
        content="hi",
        sender_id="user-1",
        sender_type="User",
        sender_name="User One",
        message_type="text",
        metadata={},
        created_at=None,
    )


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
        assert result is True  # loop continues, not a failure
        assert handler.cancelled.is_set()  # cycle was aborted
        assert "msg-1" not in handler.completed  # nothing was sent
        # Consumed: durable mark + local dedupe.
        mock_link.mark_processed.assert_awaited_once_with("room-123", "msg-1")
        assert "msg-1" in ctx.claims.completed_ids(ctx.room_id)
        assert ctx._interrupt_kind is None  # flag cleared
        assert ctx._active_cycle_task is None

        # Loop still alive: a fresh message processes normally.
        handler2 = BlockingHandler(block=False)
        ctx._on_execute = handler2
        result2 = await ctx._process_event(make_message_event(msg_id="msg-2"))
        assert result2 is True
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
        assert ctx._interrupt_kind is None

        # Next cycle runs normally.
        result = await ctx._process_event(make_message_event(msg_id="m1"))
        assert result is True
        mock_link.mark_processed.assert_awaited_once_with("room-123", "m1")


class TestStopInFlightCycle:
    async def test_stop_leaves_message_actionable(self, mock_link):
        """Stop aborts the cycle but leaves the message in 'processing' (no
        mark_processed, not remembered) so the platform replays it on play."""
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        proc = asyncio.create_task(ctx._process_event(make_message_event(msg_id="s1")))
        await handler.started.wait()

        ctx.interrupt(kind="stop")
        result = await proc

        assert result is True
        mock_link.mark_processed.assert_not_awaited()
        assert "s1" not in ctx.claims.completed_ids(ctx.room_id)
        # Local in-flight claim released so it can be reprocessed on play.
        assert "s1" not in ctx.claims.inflight_ids(ctx.room_id)


class TestShutdownVsInterrupt:
    async def test_swallowed_interrupt_does_not_leak_into_shutdown(self, mock_link):
        """A handler swallowing cancellation must not leave the control kind set."""
        first_started = asyncio.Event()

        async def swallow_cancel(ctx, event):
            first_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return

        ctx = ExecutionContext(
            "room-123", mock_link, swallow_cancel, agent_id="agent-123"
        )
        first = asyncio.create_task(
            ctx._run_cycle(make_message_event(msg_id="swallow"), "swallow")
        )
        await first_started.wait()

        assert ctx.interrupt() is True
        assert await first is True
        assert ctx._interrupt_kind is None

        await _assert_fresh_cycle_still_propagates_shutdown_cancel(ctx, "shutdown")

    async def test_shutdown_cancels_cycle_without_marking(self, mock_link):
        """stop() (shutdown) cancels an in-flight cycle, does NOT mark it
        processed, and leaves no orphaned child task."""
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        await ctx.start()

        # Feed a message through the running loop.
        await ctx.on_event(make_message_event(msg_id="sd1"))
        await handler.started.wait()

        cycle_task = ctx._active_cycle_task
        assert cycle_task is not None

        graceful = await ctx.stop()
        assert graceful is True
        mock_link.mark_processed.assert_not_awaited()  # shutdown != consume
        assert cycle_task.cancelled() or cycle_task.done()
        assert ctx._active_cycle_task is None


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
            return_value=[_backlog_message("stuck-in-processing")]
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
        replayed = _backlog_message("replayed-1")
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
        result = await ctx._process_backlog_message(_backlog_message("p1"))

        assert result == BacklogProcessResult.ADVANCED
        assert handler.completed == ["p1"]  # handler actually ran this time
        assert not ctx._retry_tracker.is_permanently_failed("p1")


class TestBacklogInterrupt:
    async def test_interrupt_during_backlog_consumes_and_advances(self, mock_link):
        """Interrupt during a /next backlog cycle consumes the message and the
        sync advances (does not retry the interrupted turn)."""
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        msg = _backlog_message("bk1")
        proc = asyncio.create_task(ctx._process_backlog_message(msg))
        await handler.started.wait()

        ctx.interrupt()
        result = await proc

        assert result == BacklogProcessResult.ADVANCED
        mock_link.mark_processed.assert_awaited_once_with("room-123", "bk1")
        assert "bk1" in ctx.claims.completed_ids(ctx.room_id)

    async def test_stop_during_backlog_leaves_actionable(self, mock_link):
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        proc = asyncio.create_task(
            ctx._process_backlog_message(_backlog_message("bk2"))
        )
        await handler.started.wait()

        ctx.interrupt(kind="stop")
        result = await proc

        assert result == BacklogProcessResult.ADVANCED
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
            return _backlog_message("loop1")

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


class TestControlSignalInClaimWindow:
    """Signals landing after a message is claimed but before its cancellable
    cycle task exists (the mark_processing/hydration window) must not be lost.
    """

    @staticmethod
    def _gated_mark_processing(
        claiming: asyncio.Event, release: asyncio.Event
    ) -> AsyncMock:
        """mark_processing that parks inside the claim so a test can fire a
        signal while the cycle task does not yet exist."""

        async def _gate(room_id: str, msg_id: str) -> bool:
            claiming.set()
            await release.wait()
            return True

        return AsyncMock(side_effect=_gate)

    async def test_interrupt_in_window_aborts_before_handler(self, mock_link):
        claiming, release = asyncio.Event(), asyncio.Event()
        mock_link.mark_processing = self._gated_mark_processing(claiming, release)
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")

        proc = asyncio.create_task(ctx._process_event(make_message_event(msg_id="w1")))
        await claiming.wait()

        # No cycle task to cancel yet, but the signal must still take effect.
        assert ctx._active_cycle_task is None
        assert ctx.interrupt() is True

        release.set()
        result = await proc

        assert result is True
        assert handler.invoked == []  # handler never ran
        mock_link.mark_processed.assert_awaited_once_with("room-123", "w1")  # consumed
        assert "w1" in ctx.claims.completed_ids(ctx.room_id)
        assert ctx._pending_interrupt is None
        assert ctx._cycle_armed is False

    async def test_stop_in_window_leaves_message_actionable(self, mock_link):
        claiming, release = asyncio.Event(), asyncio.Event()
        mock_link.mark_processing = self._gated_mark_processing(claiming, release)
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")

        proc = asyncio.create_task(ctx._process_event(make_message_event(msg_id="w2")))
        await claiming.wait()

        ctx.stop_room()  # sets _stopped and records the pending stop

        release.set()
        result = await proc

        assert result is True
        assert handler.invoked == []
        assert ctx._stopped is True
        mock_link.mark_processed.assert_not_awaited()  # left for replay on play
        assert "w2" not in ctx.claims.completed_ids(ctx.room_id)

    async def test_interrupt_in_backlog_window_aborts_and_advances(self, mock_link):
        claiming, release = asyncio.Event(), asyncio.Event()
        mock_link.mark_processing = self._gated_mark_processing(claiming, release)
        handler = BlockingHandler()
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")

        proc = asyncio.create_task(
            ctx._process_backlog_message(_backlog_message("bw1"))
        )
        await claiming.wait()

        assert ctx.interrupt() is True

        release.set()
        result = await proc

        assert result == BacklogProcessResult.ADVANCED
        assert handler.invoked == []
        mock_link.mark_processed.assert_awaited_once_with("room-123", "bw1")

    async def test_interrupt_between_cycles_arms_nothing(self, mock_link):
        """A truly idle interrupt (no claim in flight) stays a no-op and does not
        arm a pending signal that would mis-flag the next message."""
        handler = BlockingHandler(block=False)
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")

        assert ctx.interrupt() is False
        assert ctx._pending_interrupt is None

        # The next message runs to completion, unaffected.
        result = await ctx._process_event(make_message_event(msg_id="n1"))
        assert result is True
        assert handler.completed == ["n1"]


class TestControlModeValidation:
    """interrupt()'s kind argument is typed ControlMode | str -- a plain
    string still coerces, but an invalid or wrong-for-this-method value must
    be rejected at the typed boundary rather than silently misbehaving."""

    @pytest.mark.parametrize(
        "kind",
        [
            pytest.param("bogus", id="not-a-control-mode-member"),
            pytest.param(
                ControlMode.PLAY,
                id="play-is-a-valid-member-but-wrong-for-interrupt",
            ),
            pytest.param("play", id="play-as-a-plain-string"),
        ],
    )
    async def test_rejects_invalid_or_wrong_kind(self, mock_link, kind):
        ctx = ExecutionContext("room-123", mock_link, AsyncMock(), agent_id="agent-123")

        with pytest.raises(ValueError):
            ctx.interrupt(kind=kind)


class TestPendingAckCancellationGap:
    """Regression coverage for the in-process pending-ACK cancellation gap:
    once the handler has run to completion, the message must be marked
    ack-pending BEFORE the awaited mark_processed call, so a
    genuine cancellation of the enclosing task landing inside that await
    still routes redelivery through the ack-retry path instead of replaying
    the handler. Scoped to in-process cancellation with the same live
    ClaimRegistry still reachable -- not a process-restart durability
    guarantee (see execution.py's ``_abort_cycle``/step-4 docs).
    """

    @staticmethod
    def _gated_mark_processed(
        entered: asyncio.Event, release: asyncio.Event
    ) -> AsyncMock:
        """mark_processed that parks so a test can cancel the caller while
        this exact await is in flight."""

        async def _gate(room_id: str, msg_id: str) -> bool:
            entered.set()
            await release.wait()
            return True

        return AsyncMock(side_effect=_gate)

    @staticmethod
    @contextlib.asynccontextmanager
    async def _running(coro: Coroutine[Any, Any, Any]) -> AsyncIterator[asyncio.Task]:
        """Run `coro` as a task, guaranteeing it's cancelled and drained on
        exit. `_gate` above parks on `release`, which nothing sets on this
        path -- an assertion failing before the test's own explicit cancel
        would otherwise leak a task that can never finish on its own.
        """
        task = asyncio.create_task(coro)
        try:
            yield task
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_websocket_path_cancellation_during_mark_processed(self, mock_link):
        entered, release = asyncio.Event(), asyncio.Event()
        mock_link.mark_processed = self._gated_mark_processed(entered, release)
        handler = BlockingHandler(block=False)
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")

        async with self._running(
            ctx._process_event(make_message_event(msg_id="ws-cancel-ack"))
        ) as proc:
            await entered.wait()

            # The handler already ran to completion; remember_ack_pending runs
            # synchronously before this awaited mark_processed call.
            assert handler.completed == ["ws-cancel-ack"]
            assert ctx.claims.is_ack_pending("room-123", "ws-cancel-ack")
            assert not ctx.claims.is_completed("room-123", "ws-cancel-ack")

            proc.cancel()
            with pytest.raises(asyncio.CancelledError):
                await proc

        # Cancellation must not have undone the ack-pending marker -- never
        # completed, never neither.
        assert ctx.claims.is_ack_pending("room-123", "ws-cancel-ack")
        assert not ctx.claims.is_completed("room-123", "ws-cancel-ack")

        # A subsequent delivery against the same live registry retries only
        # the ack -- the handler is never re-invoked.
        mock_link.mark_processed = AsyncMock(return_value=True)
        result = await ctx._process_event(make_message_event(msg_id="ws-cancel-ack"))

        assert result is True
        assert handler.invocations == 1
        assert ctx.claims.is_completed("room-123", "ws-cancel-ack")
        assert not ctx.claims.is_ack_pending("room-123", "ws-cancel-ack")

    async def test_backlog_path_cancellation_during_mark_processed(self, mock_link):
        entered, release = asyncio.Event(), asyncio.Event()
        mock_link.mark_processed = self._gated_mark_processed(entered, release)
        handler = BlockingHandler(block=False)
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        msg = _backlog_message("bk-cancel-ack")

        async with self._running(ctx._process_backlog_message(msg)) as proc:
            await entered.wait()

            assert handler.completed == ["bk-cancel-ack"]
            assert ctx.claims.is_ack_pending("room-123", "bk-cancel-ack")
            assert not ctx.claims.is_completed("room-123", "bk-cancel-ack")

            proc.cancel()
            with pytest.raises(asyncio.CancelledError):
                await proc

        assert ctx.claims.is_ack_pending("room-123", "bk-cancel-ack")
        assert not ctx.claims.is_completed("room-123", "bk-cancel-ack")

        mock_link.mark_processed = AsyncMock(return_value=True)
        result = await ctx._process_backlog_message(msg)

        assert result == BacklogProcessResult.ADVANCED
        assert handler.invocations == 1
        assert ctx.claims.is_completed("room-123", "bk-cancel-ack")
        assert not ctx.claims.is_ack_pending("room-123", "bk-cancel-ack")


# Short enough to fire immediately against a handler that blocks; not tied to
# any real-world budget, just "small" for these deterministic tests.
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

        assert result is True  # loop continues; the watchdog is a handled error
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
        assert result2 is True
        assert handler2.completed == ["watchdog-2"]

    async def test_unset_max_cycle_seconds_never_cancels_a_slow_handler(
        self, mock_link
    ):
        """Default (unbounded) behavior is unchanged: no watchdog fires."""
        handler = BlockingHandler(block_seconds=0.05)
        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")

        result = await ctx._process_event(make_message_event(msg_id="no-watchdog"))

        assert result is True
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

        assert result is True
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

        assert result is True  # loop continues; the watchdog is a handled error
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
        stale ``_interrupt_kind`` for a later, unrelated cycle's genuine
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
        ctx._interrupt_kind = ControlMode.INTERRUPT

        result = await ctx._process_event(make_message_event(msg_id="race-1"))

        assert result is True
        assert ctx._interrupt_kind is None  # cleared, not leaked to the next cycle
        # The interrupt won, not the watchdog: consumed/acked, not failed.
        mock_link.mark_processed.assert_awaited_once_with("room-123", "race-1")
        mock_link.mark_failed.assert_not_awaited()

        await _assert_fresh_cycle_still_propagates_shutdown_cancel(ctx, "race-2")

    async def test_child_completing_at_the_deadline_boundary_is_not_misreported(
        self, mock_link, caplog
    ):
        """CPython's Task/Timeout interaction can fire the deadline's cancel on
        the *outer* task after the child has already completed successfully in
        the same event-loop tick, fabricating a CancelledError for the outer
        task's own resumption and discarding the child's real result --
        without the child itself ever actually being cancel-requested (its
        ``cancelling()`` count stays 0). The watchdog must recover the child's
        real outcome in that case rather than reporting a cycle that
        genuinely finished in time as a failure."""
        handler = BlockingHandler(block=False)
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            handler,
            agent_id="agent-123",
            config=SessionConfig(max_cycle_seconds=_AMPLE_CYCLE_BUDGET_SECONDS),
        )

        class _FakeExpiredDeadline:
            def expired(self) -> bool:
                return True

        @contextlib.asynccontextmanager
        async def fake_timeout(_seconds: float) -> AsyncIterator[_FakeExpiredDeadline]:
            # Stands in for the exact race: expired() reports True even though
            # nothing was ever actually cancelled.
            yield _FakeExpiredDeadline()

        with (
            patch("band.runtime.execution.asyncio_timeout", fake_timeout),
            caplog.at_level(logging.DEBUG, logger="band.runtime.execution"),
        ):
            result = await ctx._process_event(make_message_event(msg_id="boundary"))

        assert result is True
        mock_link.mark_processed.assert_awaited_once_with("room-123", "boundary")
        mock_link.mark_failed.assert_not_awaited()
        # The outcome above is also what a normal, un-raced completion looks
        # like -- assert the recovery branch itself actually ran (not just a
        # fall-through that never hit it), or a regression that silently
        # removes the recovery logic would still pass this test.
        assert any(
            "deadline boundary; recovering its real result" in r.message
            for r in caplog.records
        )

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

        with patch("band.runtime.execution.CYCLE_CANCEL_GRACE_SECONDS", 0.01):
            result = await asyncio.wait_for(
                ctx._process_event(make_message_event(msg_id="stuck-cleanup")),
                timeout=0.2,
            )

        assert result is True
        assert cleanup_started.is_set()
        mock_link.mark_processed.assert_not_awaited()
        mock_link.mark_failed.assert_awaited_once()
        assert "max_cycle_seconds" in mock_link.mark_failed.await_args.args[2]

        release_cleanup.set()
        await asyncio.wait_for(cleanup_finished.wait(), timeout=0.2)


@pytest.mark.parametrize("path", ["live", "backlog"])
async def test_stopped_claim_defers_without_invoking_or_poisoning_retry(
    mock_link, path: str
) -> None:
    handler = BlockingHandler(block=False)
    ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
    mock_link.mark_processing.side_effect = RoomExecutionStoppedError(ctx.room_id)
    if path == "live":
        assert not await ctx._process_event(make_message_event(msg_id="refused"))
    else:
        assert (
            await ctx._process_backlog_message(_backlog_message("refused"))
            == BacklogProcessResult.RETRY_LATER
        )
    assert handler.invoked == []
    mock_link.mark_processed.assert_not_awaited()
    mock_link.mark_failed.assert_not_awaited()
    mock_link.mark_processing.side_effect = None
    assert (
        await ctx._process_backlog_message(_backlog_message("refused"))
        == BacklogProcessResult.ADVANCED
    )
    assert handler.completed == ["refused"]


async def test_stopped_ack_retries_only_ack_after_play_beyond_budget(mock_link) -> None:
    handler = BlockingHandler(block=False)
    ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
    mock_link.mark_processed.side_effect = RoomExecutionStoppedError(ctx.room_id)
    assert not await ctx._process_event(make_message_event(msg_id="ack"))
    for _ in range(ctx.config.max_message_retries + 3):
        assert (
            await ctx._process_backlog_message(_backlog_message("ack"))
            == BacklogProcessResult.RETRY_LATER
        )
        assert ctx.claims.is_ack_pending(ctx.room_id, "ack")
        assert not ctx.claims.is_completed(ctx.room_id, "ack")
    mock_link.mark_failed.assert_not_awaited()
    mock_link.mark_processed.side_effect = None
    assert (
        await ctx._process_backlog_message(_backlog_message("ack"))
        == BacklogProcessResult.ADVANCED
    )
    assert handler.completed == ["ack"]
    assert ctx.claims.is_completed(ctx.room_id, "ack")


@pytest.mark.parametrize("post", ["message", "event"])
@pytest.mark.parametrize("suppress_cancel", [False, True])
async def test_real_stopped_post_aborts_scope_and_keeps_replayable(
    mock_link, post: str, suppress_cancel: bool
) -> None:
    peer = LifecyclePlatform()
    async with rest_client_over(peer.answer) as rest:
        mock_link.rest = rest
        invoked: list[str] = []

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            invoked.append(event.payload.id)
            tools = AgentTools.from_context(ctx)
            if len(invoked) == 1:
                try:
                    if post == "message":
                        await tools.send_message(
                            "answer", mentions=[{"id": "user-1", "handle": "user"}]
                        )
                    else:
                        await tools.execute_tool_call(
                            "band_send_event",
                            {"content": "thought", "message_type": "thought"},
                        )
                except asyncio.CancelledError:
                    if not suppress_cancel:
                        raise

        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        assert not await ctx._process_event(make_message_event(msg_id="stopped"))
        assert not ctx.is_stopped
        assert not ctx.claims.is_completed(ctx.room_id, "stopped")
        mock_link.mark_processed.assert_not_awaited()
        mock_link.mark_failed.assert_not_awaited()
        assert peer.posts == ["messages" if post == "message" else "events"]
        assert (
            await ctx._process_backlog_message(_backlog_message("stopped"))
            == BacklogProcessResult.ADVANCED
        )
        assert invoked == ["stopped", "stopped"]


async def test_old_post_cannot_cancel_new_claim_window(mock_link) -> None:
    captured: list[AgentTools] = []
    invoked: list[str] = []

    async def handler(ctx: ExecutionContext, event: Any) -> None:
        captured.append(AgentTools.from_context(ctx))
        invoked.append(event.payload.id)

    ctx = ExecutionContext("room-123", mock_link, handler)
    await ctx._process_event(make_message_event(msg_id="old"))
    claiming, release = asyncio.Event(), asyncio.Event()

    async def claim(*_: Any) -> bool:
        claiming.set()
        await release.wait()
        return True

    mock_link.mark_processing.side_effect = claim
    mock_link.rest.agent_api_events.create_agent_chat_event = AsyncMock(
        side_effect=RoomExecutionStoppedError(ctx.room_id)
    )
    task = asyncio.create_task(ctx._process_event(make_message_event(msg_id="new")))
    try:
        await claiming.wait()
        with pytest.raises(asyncio.CancelledError):
            await captured[0].send_event("late", "thought")
        release.set()
        assert await task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert invoked == ["old", "new"]


async def test_delayed_stop_after_play_does_not_notify_old_control(mock_link) -> None:
    observer = AsyncMock()
    captured: list[AgentTools] = []

    async def handler(ctx: ExecutionContext, event: Any) -> None:
        captured.append(AgentTools.from_context(ctx))

    ctx = ExecutionContext("room-123", mock_link, handler, on_platform_stop=observer)
    await ctx._process_event(make_message_event(msg_id="old"))
    await ctx.resume_room()
    mock_link.rest.agent_api_events.create_agent_chat_event = AsyncMock(
        side_effect=RoomExecutionStoppedError(ctx.room_id)
    )
    with pytest.raises(asyncio.CancelledError):
        await captured[0].send_event("delayed", "thought")
    observer.assert_not_awaited()
    assert not ctx.is_stopped
    assert await ctx._process_event(make_message_event(msg_id="new"))


async def test_detached_stop_hook_does_not_await_its_posting_task(mock_link) -> None:
    captured: list[AgentTools] = []

    async def handler(ctx: ExecutionContext, event: Any) -> None:
        captured.append(AgentTools.from_context(ctx))

    provider: asyncio.Task[None] | None = None
    cleanup = asyncio.Event()

    async def observer(ctx: ExecutionContext, scope: TurnScope) -> None:
        assert provider is not None
        provider.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await provider
        cleanup.set()

    ctx = ExecutionContext("room-123", mock_link, handler, on_platform_stop=observer)
    await ctx._process_event(make_message_event(msg_id="detached"))
    mock_link.rest.agent_api_events.create_agent_chat_event = AsyncMock(
        side_effect=RoomExecutionStoppedError(ctx.room_id)
    )
    provider = asyncio.create_task(captured[0].send_event("late", "thought"))
    with pytest.raises(asyncio.CancelledError):
        await provider
    await cleanup.wait()
    assert await ctx._process_event(make_message_event(msg_id="after"))


async def test_pending_stop_cleanup_defers_claims_with_bounded_wait(
    mock_link, monkeypatch
) -> None:
    release, entered = asyncio.Event(), asyncio.Event()

    async def observer(ctx: ExecutionContext, scope: TurnScope) -> None:
        entered.set()
        await release.wait()

    ctx = ExecutionContext(
        "room-123", mock_link, AsyncMock(), on_platform_stop=observer
    )
    await ctx._begin_scope()
    ctx.observe_platform_stop(ctx.current_scope)
    await entered.wait()
    monkeypatch.setattr("band.runtime.execution.CYCLE_CANCEL_GRACE_SECONDS", 0.01)
    assert not await ctx._process_event(make_message_event(msg_id="later"))
    mock_link.mark_processing.assert_not_awaited()
    release.set()
    assert await ctx._process_event(make_message_event(msg_id="later"))


async def test_shutdown_priority_over_observed_stop(mock_link) -> None:
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


@pytest.mark.parametrize("refusal", ["mark", "post"])
async def test_stopped_failure_reporting_preserves_room_loop(
    mock_link, refusal: str
) -> None:
    first = True

    async def handler(ctx: ExecutionContext, event: Any) -> None:
        nonlocal first
        if first:
            first = False
            raise RuntimeError("provider failed")

    ctx = ExecutionContext("room-123", mock_link, handler)
    if refusal == "mark":
        mock_link.mark_failed.side_effect = RoomExecutionStoppedError(ctx.room_id)
    else:
        mock_link.rest.agent_api_events.create_agent_chat_event = AsyncMock(
            side_effect=RoomExecutionStoppedError(ctx.room_id)
        )
    assert await ctx._process_event(make_message_event(msg_id="failure"))
    assert await ctx._process_event(make_message_event(msg_id="next"))
    mock_link.mark_processed.assert_awaited_once_with("room-123", "next")


async def test_shutdown_exits_when_stopped_provider_suppresses_cancellation(
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


async def test_observer_shutdown_without_running_room_loop(mock_link) -> None:
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


@pytest.mark.parametrize("suppress_cancel", [False, True])
@pytest.mark.parametrize("max_cycle_seconds", [None, 1.0])
async def test_newer_interrupt_consumes_a_scope_aborted_by_rest_stop(
    mock_link, suppress_cancel: bool, max_cycle_seconds: float | None
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
        mock_link.mark_processed.assert_awaited_once_with(ctx.room_id, "interrupted")

        mock_link.mark_failed.assert_not_awaited()
        await ctx._process_backlog_message(_backlog_message("interrupted"))
        assert invoked == ["interrupted"]


def _auxiliary_context(
    rest: AsyncRestClient,
    handler: Callable[[ExecutionContext, Any], Awaitable[None]],
    *,
    observer: Callable[[ExecutionContext, TurnScope], Awaitable[None]] | None = None,
    max_cycle_seconds: float | None = None,
    execution_type: type[ExecutionContext] = ExecutionContext,
) -> ExecutionContext:
    link = BandLink(agent_id=AGENT_ID, api_key="test-key")
    link.rest = rest
    return execution_type(
        ROOM_ID,
        link,
        handler,
        agent_id=AGENT_ID,
        on_platform_stop=observer,
        config=SessionConfig(
            enable_context_hydration=False,
            enable_working_state=False,
            max_message_retries=1,
            max_cycle_seconds=max_cycle_seconds,
        ),
    )


@pytest.mark.parametrize("accepted", [True, False])
async def test_auxiliary_claim_returns_acceptance_without_ending_turn(
    accepted: bool,
) -> None:
    peer = LifecyclePlatform()
    results: list[bool] = []

    def answer(request: httpx.Request) -> httpx.Response:
        if not accepted and request.url.path.endswith("/aux/processing"):
            return httpx.Response(503)
        return peer.answer(request)

    async with rest_client_over(answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            results.append(await ctx.claim_message("aux"))

        ctx = _auxiliary_context(rest, handler)
        await ctx._process_event(make_message_event(msg_id="trigger"))
        assert results == [accepted]
        assert peer.marked("processed") == ["trigger"]
        assert peer.requested("failed") == []


@pytest.mark.parametrize("path", ["live", "backlog"])
@pytest.mark.parametrize("suppress_cancel", [False, True])
async def test_auxiliary_refusal_unwinds_ownership_and_preserves_replay(
    path: str, suppress_cancel: bool
) -> None:
    peer = LifecyclePlatform()
    invoked: list[str] = []
    downstream: list[str] = []
    released: list[bool] = []
    async with rest_client_over(peer.answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            invoked.append(event.payload.id)
            owned: list[str] = []
            try:
                for mid in ("aux-1", "aux-2"):
                    assert ctx.claims.try_claim(ctx.room_id, mid)
                    owned.append(mid)
                    if len(invoked) == 1 and mid == "aux-2":
                        peer.stopped = True
                    assert await ctx.claim_message(mid)
                downstream.append(event.payload.id)
            except asyncio.CancelledError:
                if not suppress_cancel:
                    raise
            finally:
                for mid in owned:
                    ctx.claims.release(ctx.room_id, mid)
                released.append(
                    not set(owned).intersection(ctx.claims.inflight_ids(ctx.room_id))
                )

        ctx = _auxiliary_context(rest, handler)
        if path == "live":
            await ctx._process_event(make_message_event(msg_id="trigger"))
        else:
            await ctx._process_backlog_message(_backlog_message("trigger"))
        assert downstream == []
        assert peer.requested("processed") == []
        assert peer.requested("failed") == []
        assert released == [True]
        peer.stopped = False
        await ctx.resume_room()
        await ctx._process_backlog_message(_backlog_message("trigger"))
        assert downstream == ["trigger"]
        assert invoked == ["trigger", "trigger"]
        assert peer.marked("processed") == ["trigger"]
        assert released == [True, True]


@pytest.mark.parametrize("owner", ["outside", "subtask"])
async def test_auxiliary_claim_rejects_non_handler_tasks_before_rest(
    owner: str,
) -> None:
    peer = LifecyclePlatform()
    async with rest_client_over(peer.answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            with pytest.raises(RuntimeError, match="active handler"):
                await asyncio.create_task(ctx.claim_message("aux"))

        ctx = _auxiliary_context(rest, handler)
        if owner == "outside":
            with pytest.raises(RuntimeError, match="active handler"):
                await ctx.claim_message("aux")
            assert peer.requested_marks == []
        else:
            await ctx._process_event(make_message_event(msg_id="trigger"))
            assert peer.requested_marks == [
                ("trigger", "processing"),
                ("trigger", "processed"),
            ]


async def test_auxiliary_observed_stop_prevents_further_claims() -> None:
    peer = LifecyclePlatform()
    async with rest_client_over(peer.answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            peer.stopped = True
            with pytest.raises(asyncio.CancelledError):
                await ctx.claim_message("refused")
            with pytest.raises(asyncio.CancelledError):
                await ctx.claim_message("never-requested")

        ctx = _auxiliary_context(rest, handler)
        await ctx._process_event(make_message_event(msg_id="trigger"))
        assert peer.requested_marks == [
            ("trigger", "processing"),
            ("refused", "processing"),
        ]
        assert peer.requested("processed") == []
        assert peer.requested("failed") == []


@pytest.mark.parametrize("control", ["stop", "interrupt", "play", "stop-play"])
async def test_auxiliary_claim_checks_control_after_suppressed_transport_cancel(
    control: str,
) -> None:
    peer = LifecyclePlatform()
    gate = ClaimGate(peer, "aux", suppress_cancel=True)
    downstream: list[str] = []
    async with rest_client_over(gate.answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            assert await ctx.claim_message("aux")
            downstream.append(event.payload.id)

        ctx = _auxiliary_context(rest, handler)
        async with asyncio.TaskGroup() as tasks:
            processing = tasks.create_task(
                ctx._process_event(make_message_event(msg_id="trigger"))
            )
            await gate.entered.wait()
            match control:
                case "stop" | "stop-play":
                    ctx.stop_room()
                case "interrupt":
                    assert ctx.interrupt()
            if control in {"play", "stop-play"}:
                await ctx.resume_room()
            gate.release.set()
            await processing
        assert downstream == (["trigger"] if control == "play" else [])
        assert peer.marked("processed") == (
            ["trigger"] if control in {"play", "interrupt"} else []
        )
        assert peer.requested("failed") == []


@pytest.mark.parametrize("new_scope", [False, True])
@pytest.mark.parametrize("refused", [False, True])
async def test_detached_auxiliary_response_cannot_continue_or_notify_room(
    new_scope: bool, refused: bool
) -> None:
    peer = LifecyclePlatform()
    gate = ClaimGate(
        peer,
        "old-aux",
        response=httpx.Response(204) if refused else None,
        suppress_cancel=True,
    )
    old_finished, new_started, finish_new = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    downstream: list[str] = []
    notified: list[str] = []
    misuse_rejected: list[bool] = []

    async def observer(ctx: ExecutionContext, scope: TurnScope) -> None:
        notified.append(ctx.room_id)

    async with rest_client_over(gate.answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            if event.payload.id == "old":
                try:
                    await ctx.claim_message("old-aux")
                    downstream.append("old")
                except asyncio.CancelledError:
                    with pytest.raises(RuntimeError, match="active handler"):
                        await ctx.claim_message("detached")
                    misuse_rejected.append(True)
                    raise
                finally:
                    old_finished.set()
            else:
                new_started.set()
                await finish_new.wait()
                downstream.append("new")

        ctx = _auxiliary_context(rest, handler, observer=observer, max_cycle_seconds=60)
        processing = asyncio.create_task(
            ctx._process_event(make_message_event(msg_id="old"))
        )
        await gate.entered.wait()
        processing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await processing
        await gate.cancelled.wait()
        async with asyncio.TaskGroup() as tasks:
            if new_scope:
                newer = tasks.create_task(
                    ctx._process_event(make_message_event(msg_id="new"))
                )
                await new_started.wait()
            gate.release.set()
            await old_finished.wait()
            finish_new.set()
            if new_scope:
                await newer
        assert downstream == (["new"] if new_scope else [])
        assert notified == []
        assert misuse_rejected == [True]
        assert peer.marked("processed") == (["new"] if new_scope else [])
        assert peer.requested("failed") == []


async def test_auxiliary_stop_during_accepted_claim_cannot_continue() -> None:
    peer = LifecyclePlatform()
    gate = ClaimGate(peer, "aux", suppress_cancel=True)
    captured: list[AgentTools] = []
    downstream: list[str] = []
    async with rest_client_over(gate.answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            captured.append(AgentTools.from_context(ctx))
            await ctx.claim_message("aux")
            downstream.append(event.payload.id)

        ctx = _auxiliary_context(rest, handler)
        async with asyncio.TaskGroup() as tasks:
            processing = tasks.create_task(
                ctx._process_event(make_message_event(msg_id="trigger"))
            )
            await gate.entered.wait()
            with pytest.raises(asyncio.CancelledError):
                await captured[0].send_event("late", "thought")
            gate.release.set()
            await processing
        assert downstream == []
        assert peer.requested("processed") == []
        assert peer.requested("failed") == []


class ClosingExecution(ExecutionContext):
    waiting_for_idle: asyncio.Event

    async def _wait_for_idle(self, timeout: float) -> bool:
        self.waiting_for_idle.set()
        return await super()._wait_for_idle(timeout)


@pytest.mark.parametrize("graceful", [False, True])
async def test_auxiliary_claim_preserves_shutdown_priority_and_grace(
    graceful: bool,
) -> None:
    peer = LifecyclePlatform()
    peer.add_message("trigger")
    gate = ClaimGate(peer, "aux", suppress_cancel=True)
    downstream: list[str] = []
    async with rest_client_over(gate.answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            await ctx.claim_message("aux")
            downstream.append(event.payload.id)

        ctx = _auxiliary_context(
            rest, handler, max_cycle_seconds=60, execution_type=ClosingExecution
        )
        assert isinstance(ctx, ClosingExecution)
        ctx.waiting_for_idle = asyncio.Event()
        await ctx.start()
        await gate.entered.wait()
        shutdown = asyncio.create_task(ctx.stop(timeout=60 if graceful else None))
        if graceful:
            await ctx.waiting_for_idle.wait()
        else:
            await gate.cancelled.wait()
        gate.release.set()
        assert await shutdown
        assert downstream == (["trigger"] if graceful else [])
        assert not ctx.is_running


async def test_auxiliary_control_runtime_releases_then_commits_replayed_burst() -> None:
    peer = LifecyclePlatform()
    control = AuxiliaryClaimRuntime()
    control.auxiliaries.put_nowait("aux-1")
    control.auxiliaries.put_nowait("aux-2")
    control.release_claim.set()
    refuse = True

    def answer(request: httpx.Request) -> httpx.Response:
        if refuse and request.url.path.endswith("/aux-2/processing"):
            peer.stopped = True
        return peer.answer(request)

    async with rest_client_over(answer) as rest:
        ctx = _auxiliary_context(rest, control.on_execute)
        await ctx._process_event(make_message_event(msg_id="trigger"))
        assert control.cancelled.is_set()
        assert not control.owned_ids
        assert not control.downstream_started.is_set()
        assert peer.requested("processed") == []
        assert peer.requested("failed") == []
        refuse = False
        peer.stopped = False
        await ctx.resume_room()
        await ctx._process_backlog_message(_backlog_message("trigger"))
        assert control.downstream_started.is_set()
        assert control.completed_message_ids == ["trigger"]
        assert peer.marked("processed") == ["aux-1", "aux-2", "trigger"]
        assert not ctx.claims.pending_ack_ids(ctx.room_id)
        assert not control.owned_ids


async def test_live_capture_reads_durable_delivery_without_observer_frames() -> None:
    peer = LifecyclePlatform()
    peer.add_message("trigger")
    peer.messages[0]["metadata"] = {
        "delivery_status": {AGENT_ID: {"status": DeliveryStatus.PROCESSING}}
    }
    peer.add_message("unclaimed")

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": peer.messages, "metadata": {"has_more": False, "limit": 50}},
        )

    async with rest_client_over(answer) as rest:
        capture = ReplyCapture(ROOM_ID, user_ops=UserOps(rest))
        assert await capture.durable_delivery_statuses(
            ("trigger", "unclaimed"), AGENT_ID
        ) == {"trigger": DeliveryStatus.PROCESSING, "unclaimed": None}
        with pytest.raises(AssertionError, match="missing"):
            await capture.durable_delivery_statuses(("missing",), AGENT_ID)


async def test_late_rest_refusal_preserves_a_pending_explicit_interrupt(
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
        mock_link.mark_processed.assert_awaited_once_with(ctx.room_id, "interrupted")
