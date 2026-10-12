"""A retiring execution stays owned and fenced until its cleanup succeeds."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from tests.conftest import (
    make_message_event,
)
from tests.runtime.agentruntime.kit import (
    joined,
    left,
    settled,
    until_admitted,
)
from tests.runtime.helpers import RoomCleanup


class TestAgentRuntimeRoomCleanupOwnership:
    """A retiring execution stays owned and fenced until its cleanup succeeds."""

    async def test_held_cleanup_fences_rejoin_until_it_succeeds(
        self, runtime: AgentRuntime, room_cleanup: RoomCleanup, mock_handler
    ):
        await joined(runtime, "room-1")
        old = runtime.executions["room-1"]
        room_cleanup.hold()
        leaving = asyncio.create_task(runtime._on_room_left("room-1"))
        await room_cleanup.entered.wait()

        assert await runtime.rooms.admit("room-1") is None
        await runtime._on_room_event("room-1", make_message_event(room_id="room-1"))
        assert runtime.active_sessions == {}
        assert runtime.unsettled_rooms == ["room-1"]

        room_cleanup.release.set()
        await leaving
        await asyncio.wait_for(until_admitted(runtime, "room-1"), timeout=1)
        assert runtime.executions["room-1"] is not old
        assert not old.is_running
        assert runtime.unsettled_rooms == []
        mock_handler.assert_not_awaited()

    @pytest.mark.parametrize("retry", ["reconnect", "event", "rejoin"])
    async def test_failed_cleanup_is_retained_and_retried_by_admission(
        self, runtime: AgentRuntime, room_cleanup: RoomCleanup, retry: str
    ):
        await joined(runtime, "room-1")
        room_cleanup.failing = True
        await left(runtime, "room-1")
        assert runtime.unsettled_rooms == ["room-1"]

        await runtime._on_room_joined("room-1", {})
        await settled(runtime, "room-1")
        assert "room-1" not in runtime.executions

        room_cleanup.failing = False
        match retry:
            case "reconnect":
                await runtime._on_reconnected()
            case "event":
                await runtime._on_room_event(
                    "room-1", make_message_event(room_id="room-1")
                )
            case "rejoin":
                await runtime._on_room_joined("room-1", {})
        await asyncio.wait_for(until_admitted(runtime, "room-1"), timeout=1)
        assert room_cleanup.rooms == ["room-1"]
        assert runtime.unsettled_rooms == []

    async def test_newer_leave_invalidates_pending_admission(
        self, runtime: AgentRuntime, room_cleanup: RoomCleanup
    ):
        await joined(runtime, "room-1")
        room_cleanup.hold()
        leaving = asyncio.create_task(runtime._on_room_left("room-1"))
        await room_cleanup.entered.wait()
        await runtime._on_room_joined("room-1", {})
        left_again = asyncio.create_task(runtime._on_room_left("room-1"))

        room_cleanup.release.set()
        await asyncio.gather(leaving, left_again)
        await settled(runtime, "room-1")
        assert runtime.active_sessions == {}
        assert room_cleanup.rooms == ["room-1"]

    async def test_stop_attempts_every_room_and_retries_failed_cleanup(
        self, runtime: AgentRuntime, room_cleanup: RoomCleanup, mock_link
    ):
        await joined(runtime, "room-1")
        await joined(runtime, "room-2")
        room_cleanup.failing = True
        with pytest.raises(ExceptionGroup) as failures:
            await runtime.stop()
        assert len(failures.value.exceptions) == 2
        mock_link.disconnect.assert_awaited()
        assert sorted(runtime.unsettled_rooms) == ["room-1", "room-2"]

        room_cleanup.failing = False
        assert await runtime.stop()
        assert sorted(room_cleanup.rooms) == ["room-1", "room-2"]
        assert runtime.unsettled_rooms == []

    async def test_cancellation_resistant_execution_keeps_room_fenced(
        self, mock_link, room_cleanup: RoomCleanup, monkeypatch
    ):
        monkeypatch.setattr("band.runtime.execution.CYCLE_CANCEL_GRACE_SECONDS", 0.01)
        started, finish = asyncio.Event(), asyncio.Event()

        async def stubborn(ctx: ExecutionContext, event) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await finish.wait()
                raise

        runtime = AgentRuntime(
            mock_link, "agent-123", stubborn, on_session_cleanup=room_cleanup
        )
        await joined(runtime, "room-1")
        await runtime._on_room_event("room-1", make_message_event(room_id="room-1"))
        await started.wait()
        await left(runtime, "room-1")
        assert runtime.unsettled_rooms == ["room-1"]
        assert room_cleanup.rooms == []

        await runtime._on_room_joined("room-1", {})
        await settled(runtime, "room-1")
        assert "room-1" not in runtime.executions

        finish.set()
        await runtime._on_reconnected()
        await asyncio.wait_for(until_admitted(runtime, "room-1"), timeout=1)
        assert room_cleanup.rooms == ["room-1"]
        await runtime.stop()
