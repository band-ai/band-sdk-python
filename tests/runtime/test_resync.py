"""Periodic reconciliation, missed controls, and reconnect recovery."""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from band_sdk_core import RetryTracker

from band.client.streaming import ControlMode, ParticipantRemovedPayload
from band.platform.event import MessageEvent, ParticipantRemovedEvent, ReconnectedEvent
from band.platform.link import BandLink
from band.runtime.cycle import TurnScope
from band.runtime.execution import ExecutionContext, ResyncOutcome, ResyncRequest
from band.runtime.presence import RoomPresence
from band.runtime.resync import ResyncSchedule
from band.runtime.runtime import AgentRuntime
from band.runtime.types import PlatformMessage, SessionConfig
from tests.conftest import make_message_event
from tests.runtime.conftest import admit_room, wait_for_condition
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    LifecyclePlatform,
    ResponseGate,
    rest_client_over,
)

ReconciledRoom = tuple[ExecutionContext, LifecyclePlatform, list[str]]

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_link():
    """BandLink mock configured for ExecutionContext tests."""
    link = MagicMock()
    link.agent_id = "agent-123"
    link.is_connected = False

    link.connect = AsyncMock()
    link.subscribe_agent_rooms = AsyncMock()
    link.subscribe_room = AsyncMock()
    link.unsubscribe_room = AsyncMock()

    link.rest = MagicMock()
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_chats = MagicMock()
    api_response = MagicMock()
    api_response.data = []
    api_response.metadata = MagicMock()
    api_response.metadata.total_pages = None
    link.rest.agent_api_chats.list_agent_chats = AsyncMock(return_value=api_response)

    link.mark_processing = AsyncMock()
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()
    link.get_next_message = AsyncMock(return_value=None)
    link.get_stale_processing_messages = AsyncMock(return_value=[])

    async def empty_aiter():
        return
        yield

    link.__aiter__ = lambda self: empty_aiter()

    return link


@pytest.fixture
def mock_handler():
    return AsyncMock()


def make_platform_message(
    msg_id: str = "msg-1", room_id: str = "room-1"
) -> PlatformMessage:
    return PlatformMessage(
        id=msg_id,
        room_id=room_id,
        content="Hello",
        sender_id="user-999",
        sender_type="User",
        sender_name="Tester",
        message_type="text",
        metadata={},
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


@pytest_asyncio.fixture(loop_scope="function")
async def reconciled_room(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[ReconciledRoom]:
    monkeypatch.setattr(random, "uniform", lambda lower, upper: upper)
    peer = LifecyclePlatform()
    invoked: list[str] = []

    async def handler(ctx: ExecutionContext, event: Any) -> None:
        if isinstance(event, MessageEvent):
            invoked.append(event.payload.id)

    async with rest_client_over(peer.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        ctx = ExecutionContext(
            ROOM_ID,
            link,
            handler,
            agent_id=AGENT_ID,
            config=SessionConfig(
                enable_context_hydration=False, enable_working_state=False
            ),
        )
        try:
            yield ctx, peer, invoked
        finally:
            await ctx.stop()


@pytest.mark.looptime
async def test_traffic_cannot_postpone_missed_message_recovery(
    reconciled_room: ReconciledRoom,
) -> None:
    ctx, peer, invoked = reconciled_room
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    peer.add_message("missed")
    for _ in range(3):
        await asyncio.sleep(20)
        await ctx.on_event(
            ParticipantRemovedEvent(
                room_id=ROOM_ID,
                payload=ParticipantRemovedPayload(
                    id="unrelated", name="Other", type="User"
                ),
            )
        )
    await asyncio.sleep(1)
    assert invoked == ["missed"]
    assert peer.marked("processed") == ["missed"]


@pytest.mark.looptime
async def test_known_stop_recovers_after_missed_play(
    reconciled_room: ReconciledRoom,
) -> None:
    ctx, peer, invoked = reconciled_room
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    ctx.stop_room()
    peer.stopped = True
    peer.add_message("resumed")
    await asyncio.sleep(61)
    assert invoked == []
    peer.stopped = False
    await asyncio.sleep(60)
    assert invoked == ["resumed"]
    assert peer.marked("processed") == ["resumed"]


@pytest.mark.looptime
async def test_quiet_reconciliation_grows_to_conservative_cap(
    reconciled_room: ReconciledRoom,
) -> None:
    ctx, peer, invoked = reconciled_room
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    await asyncio.sleep(301)
    assert peer.next_reads == pytest.approx([0, 60, 180, 300])
    assert invoked == []


@pytest.mark.looptime
async def test_new_work_brings_backed_off_deadline_forward(
    reconciled_room: ReconciledRoom,
) -> None:
    ctx, peer, invoked = reconciled_room
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    await asyncio.sleep(200)
    peer.add_message("live")
    work_at = asyncio.get_running_loop().time()
    await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id="live"))
    await wait_for_condition(lambda: peer.marked("processed") == ["live"])
    await asyncio.sleep(61)
    assert invoked == ["live"]
    assert peer.next_reads[-1] == pytest.approx(work_at + 60)


@pytest.mark.looptime
async def test_duplicate_delivery_does_not_reset_quiet_growth(
    reconciled_room: ReconciledRoom,
) -> None:
    ctx, peer, invoked = reconciled_room
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    await asyncio.sleep(200)
    ctx.claims.remember_completed(ROOM_ID, "duplicate")
    await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id="duplicate"))
    await asyncio.sleep(61)
    assert peer.next_reads == pytest.approx([0, 60, 180])
    assert invoked == []


@pytest.mark.looptime
@pytest.mark.parametrize("signal", ["request", "reconnect", "play"])
async def test_explicit_recovery_is_immediate_after_backoff(
    reconciled_room: ReconciledRoom, signal: str
) -> None:
    ctx, peer, invoked = reconciled_room
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    await asyncio.sleep(200)
    peer.add_message("explicit")
    match signal:
        case "request":
            await ctx.request_resync()
        case "reconnect":
            await ctx.on_event(ReconnectedEvent(room_id=ROOM_ID))
        case "play":
            await ctx.resume_room()
    await wait_for_condition(lambda: peer.marked("processed") == ["explicit"])
    assert invoked == ["explicit"]
    assert peer.next_reads[-1] < 201


@pytest.mark.looptime
@pytest.mark.parametrize("signal", ["request", "reconnect"])
async def test_explicit_reconnect_probes_known_stopped_room(
    reconciled_room: ReconciledRoom, signal: str
) -> None:
    ctx, peer, invoked = reconciled_room
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    ctx.stop_room()
    peer.add_message("played")
    if signal == "request":
        await ctx.request_resync()
    else:
        await ctx.on_event(ReconnectedEvent(room_id=ROOM_ID))
    await wait_for_condition(lambda: peer.marked("processed") == ["played"])
    assert invoked == ["played"]


@pytest.mark.looptime
@pytest.mark.parametrize("mode", list(ControlMode))
async def test_control_during_stopped_probe_invalidates_response(
    reconciled_room: ReconciledRoom, mode: ControlMode
) -> None:
    ctx, peer, invoked = reconciled_room
    ctx.stop_room()
    peer.add_message("obsolete")
    gate = ResponseGate(peer, "/next")
    async with rest_client_over(gate.answer) as rest:
        ctx.link.rest = rest
        recovery = asyncio.create_task(ctx._wait_until_resync_complete())
        await gate.entered.wait()
        if mode is ControlMode.PLAY:
            await ctx.resume_room()
        else:
            ctx.interrupt(kind=mode)
        gate.release.set()
        assert await recovery is ResyncOutcome.BLOCKED
    assert invoked == []
    assert peer.requested("processing") == []


@pytest.mark.looptime
@pytest.mark.parametrize(
    "suffix", ["/pending/processed", "/obsolete/processing", "/context"]
)
async def test_new_stop_fences_recovered_candidate_across_awaits(
    reconciled_room: ReconciledRoom, suffix: str
) -> None:
    ctx, peer, invoked = reconciled_room
    ctx.stop_room()
    peer.add_message("obsolete")
    if suffix == "/pending/processed":
        ctx.claims.remember_ack_pending(ROOM_ID, "pending")
    if suffix == "/context":
        ctx.config.enable_context_hydration = True
    gate = ResponseGate(peer, suffix)
    async with rest_client_over(gate.answer) as rest:
        ctx.link.rest = rest
        recovery = asyncio.create_task(ctx._wait_until_resync_complete())
        await gate.entered.wait()
        ctx.stop_room()
        peer.stopped = True
        gate.release.set()
        outcome = await recovery
        assert outcome in {ResyncOutcome.BLOCKED, ResyncOutcome.STOPPED}
    assert invoked == []
    assert ctx.is_stopped
    assert "obsolete" not in peer.marked("processed")


@pytest.mark.looptime
async def test_stopped_ack_does_not_block_missed_play_discovery(
    reconciled_room: ReconciledRoom,
) -> None:
    ctx, peer, invoked = reconciled_room
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    ctx.claims.remember_ack_pending(ROOM_ID, "completed")
    ctx.stop_room()
    peer.stopped = True
    peer.add_message("completed")
    await asyncio.sleep(61)
    assert ctx.claims.is_ack_pending(ROOM_ID, "completed")
    assert peer.requested("processed") == []
    peer.stopped = False
    await asyncio.sleep(60)
    assert peer.marked("processed") == ["completed"]
    assert invoked == []


@pytest.mark.looptime
async def test_unchanged_completed_head_defers_without_spinning(
    reconciled_room: ReconciledRoom,
) -> None:
    ctx, peer, invoked = reconciled_room
    ctx.claims.remember_completed(ROOM_ID, "stuck")
    peer.add_message("stuck")
    assert await ctx._resync_pending_messages() is ResyncOutcome.BLOCKED
    assert len(peer.next_reads) == 2
    assert peer.requested("processed") == []
    assert invoked == []


@pytest.mark.looptime
async def test_stopped_probe_keeps_pause_on_nonrunnable_head(
    reconciled_room: ReconciledRoom,
) -> None:
    ctx, peer, invoked = reconciled_room
    ctx.claims.remember_completed(ROOM_ID, "stuck")
    peer.add_message("stuck")
    ctx.stop_room()
    peer.stopped = True
    outcome = await ctx._wait_until_resync_complete()
    assert outcome is ResyncOutcome.STOPPED
    assert ctx.is_stopped
    assert invoked == []


@pytest.mark.looptime
async def test_requests_during_reconciliation_are_not_lost(
    reconciled_room: ReconciledRoom,
) -> None:
    ctx, peer, _ = reconciled_room
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    gate = ResponseGate(peer, "/next")
    async with rest_client_over(gate.answer) as rest:
        ctx.link.rest = rest
        await ctx.request_resync()
        await gate.entered.wait()
        await ctx.request_resync()
        gate.release.set()
        await wait_for_condition(lambda: len(peer.next_reads) == 3)
    assert peer.next_reads == pytest.approx([0, 0.01, 0.01])


@pytest.mark.looptime
async def test_shutdown_cancels_blocked_probe(reconciled_room: ReconciledRoom) -> None:
    ctx, peer, invoked = reconciled_room
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    gate = ResponseGate(peer, "/next")
    async with rest_client_over(gate.answer) as rest:
        ctx.link.rest = rest
        await ctx.request_resync()
        await gate.entered.wait()
        await ctx.stop()
        gate.release.set()
        await asyncio.sleep(180)
    assert len(peer.next_reads) == 2
    assert invoked == []


@pytest.mark.looptime
async def test_stop_shortens_an_already_armed_quiet_wait(
    reconciled_room: ReconciledRoom,
) -> None:
    ctx, peer, invoked = reconciled_room
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    await asyncio.sleep(200)
    stopped_at = asyncio.get_running_loop().time()
    ctx.stop_room()
    peer.stopped = True
    await asyncio.sleep(61)
    assert peer.next_reads[-1] == pytest.approx(stopped_at + 60)
    assert invoked == []


@pytest.mark.looptime
@pytest.mark.parametrize("path", ["startup", "resync"])
async def test_retryable_handler_failure_is_retried_in_the_same_drain(
    reconciled_room: ReconciledRoom, path: str
) -> None:
    ctx, peer, invoked = reconciled_room
    peer.add_message("retryable")
    ctx._retry_tracker = RetryTracker(max_retries=3)
    ctx.config.report_turn_failures_to_room = False

    async def transient_handler(context: ExecutionContext, event: Any) -> None:
        invoked.append(event.payload.id)
        if len(invoked) == 1:
            raise RuntimeError("transient failure")

    ctx._on_execute = transient_handler
    if path == "startup":
        assert await ctx._synchronize_with_next()
    else:
        assert await ctx._resync_pending_messages() is ResyncOutcome.WORK
    assert invoked == ["retryable", "retryable"]
    assert peer.marked("processed") == ["retryable"]


@pytest.mark.looptime
async def test_new_stop_during_observer_drain_keeps_candidate_paused(
    reconciled_room: ReconciledRoom,
) -> None:
    ctx, peer, invoked = reconciled_room
    ctx.stop_room()
    peer.add_message("obsolete")
    cancelling = asyncio.Event()
    release = asyncio.Event()

    async def observer() -> None:
        try:
            await asyncio.Future[None]()
        except asyncio.CancelledError:
            cancelling.set()
            await release.wait()

    task = asyncio.create_task(observer())
    await asyncio.sleep(0)
    task.cancel()
    await cancelling.wait()
    ctx.current_scope = TurnScope(ctx._control_revision, observer_task=task)
    recovery = asyncio.create_task(ctx._wait_until_resync_complete())
    await asyncio.sleep(0)
    ctx.stop_room()
    peer.stopped = True
    release.set()
    assert await recovery is ResyncOutcome.STOPPED
    await task
    assert invoked == []
    assert peer.requested("processing") == []


@pytest.mark.looptime
async def test_due_pass_coalesces_request_before_ready_events(
    reconciled_room: ReconciledRoom,
) -> None:
    ctx, peer, invoked = reconciled_room
    started = asyncio.Event()
    release = asyncio.Event()
    handled: list[str] = []

    async def handler(context: ExecutionContext, event: Any) -> None:
        handled.append(event.payload.id)
        if isinstance(event, ParticipantRemovedEvent):
            started.set()
            await release.wait()
        else:
            invoked.append(event.payload.id)

    ctx._on_execute = handler
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    participant = ParticipantRemovedEvent(
        room_id=ROOM_ID,
        payload=ParticipantRemovedPayload(id="other", name="Other", type="User"),
    )
    await ctx.on_event(participant)
    await started.wait()
    peer.add_message("missed")
    await ctx.on_event(participant)
    await ctx.request_resync()
    await asyncio.sleep(61)
    release.set()
    await asyncio.sleep(1)
    assert len(peer.next_reads) == 3
    assert invoked == ["missed"]
    assert handled == ["other", "missed", "other"]


@pytest.mark.looptime
async def test_deadline_does_not_cancel_handler_or_issue_catchup_burst(
    reconciled_room: ReconciledRoom,
) -> None:
    ctx, peer, invoked = reconciled_room
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(context: ExecutionContext, event: Any) -> None:
        started.set()
        await release.wait()
        invoked.append(event.payload.id)

    ctx._on_execute = handler
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    peer.add_message("slow")
    await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id="slow"))
    await started.wait()
    await asyncio.sleep(301)
    assert invoked == []
    assert len(peer.next_reads) == 1
    release.set()
    await asyncio.sleep(1)
    assert invoked == ["slow"]
    assert peer.marked("processed") == ["slow"]
    assert len(peer.next_reads) == 2


@pytest.mark.looptime
async def test_custom_base_above_default_maximum_keeps_its_interval(
    reconciled_room: ReconciledRoom,
) -> None:
    ctx, peer, _ = reconciled_room
    ctx.config = SessionConfig(idle_resync_seconds=300, enable_working_state=False)
    ctx._resync_schedule = ResyncSchedule(300, 120)
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    await asyncio.sleep(601)
    assert peer.next_reads == pytest.approx([0, 300, 600])


@pytest.mark.looptime
async def test_initial_spread_does_not_shorten_recurring_intervals(
    reconciled_room: ReconciledRoom, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, peer, _ = reconciled_room
    monkeypatch.setattr(random, "uniform", lambda lower, upper: upper / 2)
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    await asyncio.sleep(271)
    assert peer.next_reads == pytest.approx([0, 30, 150, 270])


@pytest.mark.looptime
async def test_http_error_retains_base_retry_cadence(
    reconciled_room: ReconciledRoom,
) -> None:
    ctx, peer, invoked = reconciled_room
    await ctx.start()
    await wait_for_condition(lambda: ctx._sync_complete)
    failure_pending = True

    def answer(request: httpx.Request) -> httpx.Response:
        nonlocal failure_pending
        response = peer.answer(request)
        if request.url.path.endswith("/next") and failure_pending:
            failure_pending = False
            return httpx.Response(400)
        return response

    async with rest_client_over(answer) as rest:
        ctx.link.rest = rest
        await asyncio.sleep(241)
    assert peer.next_reads == pytest.approx([0, 60, 120, 240])
    assert invoked == []


@pytest.mark.parametrize("field", ["idle_resync_seconds", "idle_resync_max_seconds"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), -float("inf")])
def test_unsafe_reconciliation_intervals_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValueError, match=field):
        SessionConfig(**{field: value})


# ---------------------------------------------------------------------------
# TestRequestResync
# ---------------------------------------------------------------------------


class TestRequestResync:
    """Tests for ExecutionContext.request_resync()."""

    async def test_enqueues_resync_sentinel(self, mock_link, mock_handler):
        """request_resync() should put a ResyncRequest onto the queue."""
        ctx = ExecutionContext("room-1", mock_link, mock_handler)

        await ctx.request_resync()

        assert ctx.queue.qsize() == 1
        item = ctx.queue.get_nowait()
        assert isinstance(item, ResyncRequest)

    async def test_sentinel_triggers_resync(self, mock_link, mock_handler):
        """Enqueueing a sentinel should cause the Phase 2 loop to call /next."""
        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        await ctx.start()

        await wait_for_condition(lambda: ctx._sync_complete)

        call_count_before = mock_link.get_next_message.call_count

        await ctx.request_resync()
        await wait_for_condition(
            lambda: mock_link.get_next_message.call_count > call_count_before
        )

        # At least one more /next call should have occurred
        assert mock_link.get_next_message.call_count > call_count_before

        await ctx.stop()

    async def test_multiple_resyncs_dont_crash(self, mock_link, mock_handler):
        """Multiple rapid request_resync() calls should not crash or deadlock."""
        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)

        for _ in range(5):
            await ctx.request_resync()

        await wait_for_condition(lambda: ctx.queue.qsize() == 0)
        # Still running, no exception
        assert ctx.is_running

        await ctx.stop()


# ---------------------------------------------------------------------------
# TestIdleTimeout
# ---------------------------------------------------------------------------


class TestIdleTimeout:
    """Tests for idle-timeout resync in Phase 2 loop."""

    async def test_idle_timeout_triggers_resync(self, mock_link, mock_handler):
        """Phase 2 should call /next after idle_resync_seconds with no WS events."""
        config = SessionConfig(idle_resync_seconds=0.01)  # fast timeout for test
        ctx = ExecutionContext("room-1", mock_link, mock_handler, config=config)
        await ctx.start()

        await wait_for_condition(
            lambda: mock_link.get_next_message.call_count >= 2,
            timeout=1.0,
        )

        # get_next_message is called during Phase 1 AND during idle timeout resync
        assert mock_link.get_next_message.call_count >= 2

        await ctx.stop()

    async def test_websocket_work_does_not_force_an_immediate_poll(
        self, mock_link, mock_handler, monkeypatch
    ):
        monkeypatch.setattr(random, "uniform", lambda lower, upper: upper)
        config = SessionConfig(idle_resync_seconds=60)  # very long timeout
        ctx = ExecutionContext("room-1", mock_link, mock_handler, config=config)
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)

        call_count_after_phase1 = mock_link.get_next_message.call_count

        # New work restores the base interval without postponing the deadline.
        event = make_message_event(room_id="room-1", msg_id="msg-x")
        await ctx.on_event(event)
        await wait_for_condition(lambda: mock_handler.await_count >= 1)

        # No idle timeout should have fired (timeout is 60s)
        assert mock_link.get_next_message.call_count == call_count_after_phase1

        await ctx.stop()


# ---------------------------------------------------------------------------
# TestResyncPendingMessages
# ---------------------------------------------------------------------------


class TestResyncPendingMessages:
    """Tests for ExecutionContext._resync_pending_messages()."""

    async def test_empty_returns_immediately(self, mock_link, mock_handler):
        """/next returning None immediately should not call the handler."""
        mock_link.get_next_message.return_value = None

        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        # Call directly without starting the loop
        await ctx._resync_pending_messages()

        mock_handler.assert_not_called()

    async def test_processes_single_missed_message(self, mock_link, mock_handler):
        """/next returning one message then None should process that message."""
        msg = make_platform_message(msg_id="missed-1", room_id="room-1")
        # First call returns the missed message, second returns None
        mock_link.get_next_message.side_effect = [msg, None]

        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)

        # Reset state so _resync_pending_messages runs fresh
        mock_link.get_next_message.side_effect = [msg, None]
        await ctx._resync_pending_messages()

        # Handler should have been called for the missed message
        mock_handler.assert_called()

        await ctx.stop()

    async def test_skips_duplicate_message(self, mock_link, mock_handler):
        """A message already recorded as completed should be skipped."""
        msg = make_platform_message(msg_id="dup-1", room_id="room-1")
        mock_link.get_next_message.side_effect = [msg, None]

        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        # Mark the message as already processed
        ctx.claims.remember_completed(ctx.room_id, "dup-1")

        await ctx._resync_pending_messages()

        mock_handler.assert_not_called()

    async def test_exception_does_not_propagate(self, mock_link, mock_handler):
        """Exceptions inside _resync_pending_messages should be caught, not raised."""
        mock_link.get_next_message.side_effect = RuntimeError("API down")

        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        # Should complete without raising
        await ctx._resync_pending_messages()


# ---------------------------------------------------------------------------
# TestAgentRuntimeOnReconnected
# ---------------------------------------------------------------------------


class TestAgentRuntimeOnReconnected:
    """Tests for AgentRuntime._on_reconnected()."""

    async def test_calls_request_resync_on_all_executions(
        self, mock_link, mock_handler
    ):
        """_on_reconnected() should call request_resync() on each execution."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        exec1 = MagicMock()
        exec1.request_resync = AsyncMock()
        exec2 = MagicMock()
        exec2.request_resync = AsyncMock()

        runtime.executions = {"room-1": exec1, "room-2": exec2}

        await runtime._on_reconnected()

        exec1.request_resync.assert_called_once()
        exec2.request_resync.assert_called_once()

    async def test_skips_execution_without_request_resync(
        self, mock_link, mock_handler
    ):
        """_on_reconnected() should not raise if an execution lacks request_resync."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        # Simulate a legacy/custom Execution without the new method
        legacy_exec = MagicMock(spec=[])  # spec=[] → no attributes at all

        runtime.executions = {"room-legacy": legacy_exec}

        # Should not raise AttributeError
        await runtime._on_reconnected()

    async def test_one_failure_does_not_abort_others(self, mock_link, mock_handler):
        """A failing request_resync() should not prevent the others from running."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        exec1 = MagicMock()
        exec1.request_resync = AsyncMock(side_effect=RuntimeError("boom"))
        exec2 = MagicMock()
        exec2.request_resync = AsyncMock()

        runtime.executions = {"room-1": exec1, "room-2": exec2}

        await runtime._on_reconnected()

        exec2.request_resync.assert_called_once()

    async def test_presence_reconnect_still_resyncs_active_executions_if_api_fails(
        self, mock_link, mock_handler
    ):
        """The full presence -> runtime callback chain should survive API failure."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        execution = MagicMock()
        execution.request_resync = AsyncMock()
        runtime.executions = {"room-1": execution}

        mock_link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            side_effect=RuntimeError("network error")
        )

        await runtime.presence._handle_reconnect()

        execution.request_resync.assert_called_once()


# ---------------------------------------------------------------------------
# TestPresenceReconnectOnReconnectedCallback
# ---------------------------------------------------------------------------


class TestPresenceReconnectOnReconnectedCallback:
    """Tests that on_reconnected fires reliably from _handle_reconnect."""

    @pytest.fixture
    def mock_presence_link(self):
        link = MagicMock()
        link.agent_id = "agent-123"
        link.is_connected = False
        link.connect = AsyncMock()
        link.subscribe_agent_rooms = AsyncMock()
        link.subscribe_room = AsyncMock()
        link.unsubscribe_room = AsyncMock()

        link.rest = MagicMock()
        link.rest.agent_api_chats = MagicMock()

        # Return a properly structured response so _list_existing_rooms terminates
        # correctly (total_pages=None breaks the pagination loop after one call).
        api_response = MagicMock()
        api_response.data = []
        api_response.metadata = MagicMock()
        api_response.metadata.total_pages = None
        link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            return_value=api_response
        )

        async def empty_aiter():
            return
            yield

        link.__aiter__ = lambda self: empty_aiter()
        return link

    async def test_on_reconnected_fires_with_auto_subscribe_true(
        self, mock_presence_link
    ):
        """on_reconnected fires after reconnect when auto_subscribe_existing=True."""
        reconnected_calls = []

        async def on_reconnected():
            reconnected_calls.append(1)

        presence = RoomPresence(mock_presence_link, auto_subscribe_existing=True)
        presence.on_reconnected = on_reconnected

        await presence._handle_reconnect()

        assert len(reconnected_calls) == 1

    async def test_on_reconnected_fires_with_auto_subscribe_false(
        self, mock_presence_link
    ):
        """on_reconnected fires after reconnect even when auto_subscribe_existing=False.

        The finally block ensures the callback always fires regardless of early
        returns in the try block (which exits early when auto_subscribe_existing=False).
        """
        reconnected_calls = []

        async def on_reconnected():
            reconnected_calls.append(1)

        presence = RoomPresence(mock_presence_link, auto_subscribe_existing=False)
        presence.on_reconnected = on_reconnected

        await presence._handle_reconnect()

        assert len(reconnected_calls) == 1

    async def test_on_reconnected_still_fires_if_api_fails(self, mock_presence_link):
        """on_reconnected should still fire if room reconciliation fails.

        Existing executions still need a /next resync even when the room-list API
        is temporarily unavailable during reconnect.
        """
        reconnected_calls = []

        async def on_reconnected():
            reconnected_calls.append(1)

        mock_presence_link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            side_effect=RuntimeError("network error")
        )

        presence = RoomPresence(mock_presence_link, auto_subscribe_existing=True)
        presence.on_reconnected = on_reconnected

        await presence._handle_reconnect()

        assert len(reconnected_calls) == 1

    async def test_on_reconnected_still_fires_if_unsubscribe_fails(
        self, mock_presence_link
    ):
        """on_reconnected should still fire if a stale room unsubscribe fails."""
        reconnected_calls = []

        async def on_reconnected():
            reconnected_calls.append(1)

        room = MagicMock()
        room.id = "room-new"
        room.model_dump.return_value = {"id": "room-new"}

        api_response = MagicMock()
        api_response.data = [room]
        api_response.metadata = MagicMock()
        api_response.metadata.total_pages = None
        mock_presence_link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            return_value=api_response
        )
        mock_presence_link.unsubscribe_room = AsyncMock(
            side_effect=RuntimeError("unsubscribe failed")
        )

        presence = RoomPresence(mock_presence_link, auto_subscribe_existing=True)
        admit_room(presence, "room-old")
        presence.on_reconnected = on_reconnected

        await presence._handle_reconnect()

        assert len(reconnected_calls) == 1

    async def test_cancelled_error_propagates_from_callback(self, mock_presence_link):
        """CancelledError raised in on_reconnected must propagate (structured concurrency)."""

        async def on_reconnected_that_raises():
            raise asyncio.CancelledError

        presence = RoomPresence(mock_presence_link, auto_subscribe_existing=False)
        presence.on_reconnected = on_reconnected_that_raises

        with pytest.raises(asyncio.CancelledError):
            await presence._handle_reconnect()
