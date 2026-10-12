"""A retiring execution stays owned and fenced until its cleanup succeeds."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from tests.conftest import (
    make_message_event,
    make_room_added_event,
    make_room_removed_event,
)
from tests.runtime.agentruntime.kit import (
    QueueLink,
    StartGate,
    joined,
    left,
    settled,
    until_admitted,
)
from tests.runtime.conftest import wait_for_condition
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


async def test_stop_retry_keeps_an_earlier_cancelled_room_reported(
    held_runtime: AgentRuntime, start_gate: StartGate, room_cleanup: RoomCleanup
):
    await joined(held_runtime, "room-1")
    await joined(held_runtime, "room-2")
    start_gate.graceful = False
    room_cleanup.failing_rooms = {"room-2"}
    with pytest.raises(ExceptionGroup):
        await held_runtime.stop()

    start_gate.graceful = True
    room_cleanup.failing_rooms = set()
    assert await held_runtime.stop() is False
    assert sorted(room_cleanup.rooms) == ["room-1", "room-2"]


async def test_held_room_cleanup_does_not_block_other_rooms_events(
    mock_link, mock_handler, room_cleanup: RoomCleanup
):
    feed = QueueLink(mock_link)
    handled = asyncio.Event()
    mock_handler.side_effect = lambda ctx, event: handled.set()
    runtime = AgentRuntime(
        mock_link, "agent-123", mock_handler, on_session_cleanup=room_cleanup
    )
    await runtime.start()
    try:
        feed.events.put_nowait(make_room_added_event("room-1"))
        feed.events.put_nowait(make_room_added_event("room-2"))
        await wait_for_condition(lambda: "room-2" in runtime.executions)

        room_cleanup.hold()
        feed.events.put_nowait(make_room_removed_event("room-1"))
        await room_cleanup.entered.wait()
        feed.events.put_nowait(make_message_event(room_id="room-2", msg_id="m-2"))
        await asyncio.wait_for(handled.wait(), timeout=1)
        assert room_cleanup.rooms == []
        assert "room-1" not in runtime.executions
    finally:
        room_cleanup.release.set()
        await runtime.stop()
