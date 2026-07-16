"""Tests for ExecutionContext per-cycle interrupt/stop.

The reasoning cycle runs as a cancellable child task so a control signal can
abort just one turn without killing the room's process loop. These tests drive
``_process_event`` directly with a controllable ``on_execute`` so we can cancel
mid-cycle deterministically.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.execution import ExecutionContext, _BacklogProcessResult
from band.runtime.types import PlatformMessage, SessionConfig
from tests.conftest import make_message_event


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
    return link


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
        started = asyncio.Event()
        sent: list[str] = []

        async def on_execute(ctx, event):
            started.set()
            try:
                await asyncio.sleep(60)  # simulate a long reasoning cycle
                sent.append(event.payload.id)  # would "send" — must not happen
            except asyncio.CancelledError:
                sent.append("CANCELLED")
                raise

        ctx = ExecutionContext("room-123", mock_link, on_execute, agent_id="agent-123")

        event = make_message_event(msg_id="msg-1")
        proc = asyncio.create_task(ctx._process_event(event))
        await started.wait()

        # Interrupt from the "receive task" side.
        assert ctx.interrupt() is True

        result = await proc
        assert result is True  # loop continues, not a failure
        assert "msg-1" not in sent  # nothing was sent
        # Consumed: durable mark + local dedupe.
        mock_link.mark_processed.assert_awaited_once_with("room-123", "msg-1")
        assert "msg-1" in ctx._processed_ids
        assert ctx._interrupt_kind is None  # flag cleared
        assert ctx._active_cycle_task is None

        # Loop still alive: a fresh message processes normally.
        completed: list[str] = []

        async def on_execute2(ctx, event):
            completed.append(event.payload.id)

        ctx._on_execute = on_execute2
        result2 = await ctx._process_event(make_message_event(msg_id="msg-2"))
        assert result2 is True
        assert completed == ["msg-2"]

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
        started = asyncio.Event()

        async def on_execute(ctx, event):
            started.set()
            await asyncio.sleep(60)

        ctx = ExecutionContext("room-123", mock_link, on_execute, agent_id="agent-123")
        proc = asyncio.create_task(ctx._process_event(make_message_event(msg_id="s1")))
        await started.wait()

        ctx.interrupt(kind="stop")
        result = await proc

        assert result is True
        mock_link.mark_processed.assert_not_awaited()
        assert "s1" not in ctx._processed_ids
        # Local in-flight claim released so it can be reprocessed on play.
        assert "s1" not in ctx._inflight_message_ids


class TestShutdownVsInterrupt:
    async def test_shutdown_cancels_cycle_without_marking(self, mock_link):
        """stop() (shutdown) cancels an in-flight cycle, does NOT mark it
        processed, and leaves no orphaned child task."""
        started = asyncio.Event()

        async def on_execute(ctx, event):
            started.set()
            await asyncio.sleep(60)

        ctx = ExecutionContext("room-123", mock_link, on_execute, agent_id="agent-123")
        await ctx.start()

        # Feed a message through the running loop.
        await ctx.on_event(make_message_event(msg_id="sd1"))
        await started.wait()

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
        started = asyncio.Event()

        async def on_execute(ctx, event):
            started.set()
            await asyncio.sleep(60)

        ctx = ExecutionContext("room-123", mock_link, on_execute, agent_id="agent-123")
        proc = asyncio.create_task(ctx._process_event(make_message_event(msg_id="x")))
        await started.wait()

        ctx.stop_room()
        result = await proc

        assert ctx._stopped is True
        assert result is True
        mock_link.mark_processed.assert_not_awaited()

    async def test_stopped_room_skips_new_message(self, mock_link):
        """A WS trigger arriving while stopped is left actionable (never claimed
        or marked), not processed."""
        executed: list[str] = []

        async def on_execute(ctx, event):
            executed.append(event.payload.id)

        ctx = ExecutionContext("room-123", mock_link, on_execute, agent_id="agent-123")
        ctx._stopped = True

        result = await ctx._process_event(make_message_event(msg_id="while-stopped"))

        assert result is True
        assert executed == []  # adapter never invoked
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

    async def test_stale_recovery_skipped_while_stopped(self, mock_link):
        """Reconnect-while-stopped must NOT resurrect the interrupted message via
        the stale-processing recovery sweep (stop-survives-reconnect, locally
        guaranteed — not reliant on the platform mark gate)."""
        mock_link.get_stale_processing_messages = AsyncMock(
            return_value=[_backlog_message("stuck-in-processing")]
        )
        executed: list[str] = []

        async def on_execute(ctx, event):
            executed.append(event.payload.id)

        ctx = ExecutionContext("room-123", mock_link, on_execute, agent_id="agent-123")
        ctx._stopped = True

        ok = await ctx._recover_stale_processing_messages()

        assert ok is True
        assert executed == []  # adapter not invoked while stopped
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
        executed: list[str] = []

        async def on_execute(ctx, event):
            executed.append(event.payload.id)

        ctx = ExecutionContext("room-123", mock_link, on_execute, agent_id="agent-123")
        # Simulate: was stopped, now resuming.
        ctx._stopped = False
        ok = await ctx._resync_pending_messages()

        assert ok is True
        assert executed == ["replayed-1"]


class TestBacklogInterrupt:
    async def test_interrupt_during_backlog_consumes_and_advances(self, mock_link):
        """Interrupt during a /next backlog cycle consumes the message and the
        sync advances (does not retry the interrupted turn)."""
        started = asyncio.Event()

        async def on_execute(ctx, event):
            started.set()
            await asyncio.sleep(60)

        ctx = ExecutionContext("room-123", mock_link, on_execute, agent_id="agent-123")
        msg = _backlog_message("bk1")
        proc = asyncio.create_task(ctx._process_backlog_message(msg))
        await started.wait()

        ctx.interrupt()
        result = await proc

        assert result == _BacklogProcessResult.ADVANCED
        mock_link.mark_processed.assert_awaited_once_with("room-123", "bk1")
        assert "bk1" in ctx._processed_ids

    async def test_stop_during_backlog_leaves_actionable(self, mock_link):
        started = asyncio.Event()

        async def on_execute(ctx, event):
            started.set()
            await asyncio.sleep(60)

        ctx = ExecutionContext("room-123", mock_link, on_execute, agent_id="agent-123")
        proc = asyncio.create_task(
            ctx._process_backlog_message(_backlog_message("bk2"))
        )
        await started.wait()

        ctx.interrupt(kind="stop")
        result = await proc

        assert result == _BacklogProcessResult.ADVANCED
        mock_link.mark_processed.assert_not_awaited()
        assert "bk2" not in ctx._processed_ids

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

        started = asyncio.Event()
        calls: list[str] = []

        async def on_execute(ctx, event):
            calls.append(event.payload.id)
            started.set()
            await asyncio.sleep(60)  # cancelled by stop_room() below

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            on_execute,
            agent_id="agent-123",
            # A high retry cap so the retry tracker can't itself block a second
            # attempt -- the test must fail (or pass) on the _stopped guard
            # alone, not be masked by the unrelated max-retries limit.
            config=SessionConfig(max_message_retries=10),
        )

        resync_task = asyncio.create_task(ctx._resync_pending_messages())
        await started.wait()

        ctx.stop_room()
        result = await asyncio.wait_for(resync_task, timeout=5)

        assert result is True
        # The adapter must not run a second time on the very next /next poll.
        assert calls == ["loop1"]
        mock_link.mark_processed.assert_not_awaited()
        assert "loop1" not in ctx._processed_ids
