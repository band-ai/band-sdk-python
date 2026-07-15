"""Tests for AgentRuntime.handle_control routing/dedup."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.client.streaming import AgentControlPayload
from band.runtime.runtime import AgentRuntime


def _control(mode: str, scope: str = "agent", **kw) -> AgentControlPayload:
    return AgentControlPayload(mode=mode, scope=scope, agent_id="agent-123", **kw)


def _fake_execution(room_id: str) -> MagicMock:
    """A control-capable execution: interrupt/stop_room sync, resume_room async."""
    ex = MagicMock()
    ex.room_id = room_id
    ex.interrupt = MagicMock(return_value=True)
    ex.stop_room = MagicMock()
    ex.resume_room = AsyncMock()
    return ex


@pytest.fixture
def runtime() -> AgentRuntime:
    link = MagicMock()
    return AgentRuntime(link=link, agent_id="agent-123", on_execute=AsyncMock())


class TestRouting:
    async def test_agent_scope_null_room_fans_out_to_all(self, runtime):
        a, b = _fake_execution("r1"), _fake_execution("r2")
        runtime.executions = {"r1": a, "r2": b}

        await runtime.handle_control(_control("interrupt", scope="agent", room_id=None))

        a.interrupt.assert_called_once()
        b.interrupt.assert_called_once()

    async def test_room_scope_targets_single_room(self, runtime):
        a, b = _fake_execution("r1"), _fake_execution("r2")
        runtime.executions = {"r1": a, "r2": b}

        await runtime.handle_control(_control("stop", scope="room", room_id="r2"))

        a.stop_room.assert_not_called()
        b.stop_room.assert_called_once()

    async def test_agent_scope_with_room_id_targets_that_room(self, runtime):
        a, b = _fake_execution("r1"), _fake_execution("r2")
        runtime.executions = {"r1": a, "r2": b}

        await runtime.handle_control(_control("interrupt", scope="agent", room_id="r1"))

        a.interrupt.assert_called_once()
        b.interrupt.assert_not_called()

    async def test_unknown_room_is_noop(self, runtime):
        a = _fake_execution("r1")
        runtime.executions = {"r1": a}

        await runtime.handle_control(
            _control("interrupt", scope="room", room_id="ghost")
        )

        a.interrupt.assert_not_called()

    async def test_play_routes_to_resume_room(self, runtime):
        a = _fake_execution("r1")
        runtime.executions = {"r1": a}

        await runtime.handle_control(_control("play", scope="room", room_id="r1"))

        a.resume_room.assert_awaited_once()


class TestDedup:
    async def test_duplicate_correlation_id_dropped(self, runtime):
        a = _fake_execution("r1")
        runtime.executions = {"r1": a}
        sig = _control("interrupt", scope="room", room_id="r1", correlation_id="ctl-1")

        await runtime.handle_control(sig)
        await runtime.handle_control(sig)  # duplicate

        a.interrupt.assert_called_once()

    async def test_distinct_correlation_ids_both_applied(self, runtime):
        a = _fake_execution("r1")
        runtime.executions = {"r1": a}

        await runtime.handle_control(
            _control("interrupt", scope="room", room_id="r1", correlation_id="ctl-1")
        )
        await runtime.handle_control(
            _control("interrupt", scope="room", room_id="r1", correlation_id="ctl-2")
        )

        assert a.interrupt.call_count == 2

    async def test_play_after_stop_not_dropped(self, runtime):
        """Distinct signals have distinct correlation_ids; play after stop runs."""
        a = _fake_execution("r1")
        runtime.executions = {"r1": a}

        await runtime.handle_control(
            _control("stop", scope="room", room_id="r1", correlation_id="ctl-stop")
        )
        await runtime.handle_control(
            _control("play", scope="room", room_id="r1", correlation_id="ctl-play")
        )

        a.stop_room.assert_called_once()
        a.resume_room.assert_awaited_once()

    async def test_missing_correlation_id_not_deduped(self, runtime):
        a = _fake_execution("r1")
        runtime.executions = {"r1": a}
        sig = _control("interrupt", scope="room", room_id="r1")  # no correlation_id

        await runtime.handle_control(sig)
        await runtime.handle_control(sig)

        assert a.interrupt.call_count == 2  # both applied (cannot dedup)


class TestStopSurvivesReconnect:
    async def test_reconnect_while_stopped_does_not_invoke_adapter(self):
        """After stop, a reconnect (ReconnectedEvent -> /next sync incl. stale
        recovery) must NOT re-fire the adapter. Needs no SDK persistence — the
        local _stopped guard + platform /next->204 keep the room quiet.

        Asserts the adapter is NOT invoked (covers both the no-persistence claim
        and the recovery-sweep guard), per architect's Step-4 should-fix.
        """
        from band.platform.event import ReconnectedEvent
        from band.runtime.execution import ExecutionContext

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
        # Platform /next gate returns 204 (None) for a stopped agent — that path
        # is platform-authoritative. The LOCAL risk is the recovery sweep, which
        # fetches 'processing' messages DIRECTLY (bypassing /next): the stop path
        # leaves the interrupted message there. The _stopped guard must skip it.
        from band.runtime.types import PlatformMessage

        stuck = PlatformMessage(
            id="stuck",
            room_id="room-123",
            content="hi",
            sender_id="u1",
            sender_type="User",
            sender_name="U1",
            message_type="text",
            metadata={},
            created_at=None,
        )
        link.get_stale_processing_messages = AsyncMock(return_value=[stuck])
        link.get_next_message = AsyncMock(return_value=None)  # /next gate: 204

        executed: list[str] = []

        async def on_execute(ctx, event):
            executed.append(event.payload.id)

        ctx = ExecutionContext("room-123", link, on_execute, agent_id="agent-123")
        ctx._stopped = True
        ctx._reconnect_sync_requested = True

        # Drive the reconnect sync path directly.
        await ctx._process_event(ReconnectedEvent())

        assert executed == []  # adapter never invoked while stopped
        link.mark_processing.assert_not_awaited()
        # Recovery sweep (the gate-bypassing path) was skipped locally.
        link.get_stale_processing_messages.assert_not_awaited()


class TestGracefulDegradation:
    async def test_custom_execution_without_methods_is_skipped(self, runtime):
        """A custom Execution lacking the control methods degrades to no-op."""

        class BareExecution:
            room_id = "r1"

        runtime.executions = {"r1": BareExecution()}

        # Must not raise for any mode.
        await runtime.handle_control(_control("interrupt", scope="room", room_id="r1"))
        await runtime.handle_control(_control("stop", scope="room", room_id="r1"))
        await runtime.handle_control(_control("play", scope="room", room_id="r1"))
