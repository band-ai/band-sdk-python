"""Execution startup is owned with retirement and routed only when ready."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.runtime import AgentRuntime
from tests.conftest import (
    make_message_event,
    make_room_added_event,
    make_room_removed_event,
)
from tests.runtime.agentruntime.kit import (
    QueueLink,
    joined,
    settled,
    until_admitted,
)
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import RoomCleanup, StartGate


class TestAgentRuntimeStartupOwnership:
    """Execution startup is owned with retirement and routed only when ready."""

    @pytest.mark.parametrize("withdrawal", ["leave", "stop"])
    async def test_held_start_never_routes_after_withdrawal(
        self,
        held_runtime: AgentRuntime,
        start_gate: StartGate,
        room_cleanup: RoomCleanup,
        withdrawal: str,
    ):
        start_gate.hold()
        joining = asyncio.create_task(held_runtime._on_room_joined("room-1", {}))
        await start_gate.entered.wait()
        assert held_runtime.active_sessions == {}

        if withdrawal == "leave":
            await held_runtime._on_room_left("room-1")
            withdrawn = asyncio.ensure_future(settled(held_runtime, "room-1"))
        else:
            withdrawn = asyncio.create_task(held_runtime.stop())
        await wait_for_condition(lambda: not held_runtime.rooms.wanted("room-1"))
        (execution,) = start_gate.built
        assert not withdrawn.done() and not execution.stopped

        start_gate.release.set()
        await asyncio.gather(joining, withdrawn)
        assert execution.started and execution.stopped
        assert execution.resyncs == 0
        assert held_runtime.active_sessions == {}
        assert room_cleanup.rooms == ["room-1"]

    async def test_event_before_first_startup_is_recovered_by_resync(
        self, held_runtime: AgentRuntime, start_gate: StartGate
    ):
        start_gate.hold()
        await held_runtime._on_room_joined("room-1", {})
        await start_gate.entered.wait()
        await held_runtime._on_room_event(
            "room-1", make_message_event(room_id="room-1")
        )

        start_gate.release.set()
        await settled(held_runtime, "room-1")
        (execution,) = start_gate.built
        assert held_runtime.executions["room-1"] is execution
        assert execution.events == []
        assert execution.resyncs == 1

    async def test_failed_start_is_not_routed_and_is_retried(
        self, held_runtime: AgentRuntime, start_gate: StartGate
    ):
        start_gate.failures = 1
        await joined(held_runtime, "room-1")
        assert held_runtime.active_sessions == {}
        assert held_runtime.unsettled_rooms == ["room-1"]

        await held_runtime._on_room_event(
            "room-1", make_message_event(room_id="room-1")
        )
        await asyncio.wait_for(until_admitted(held_runtime, "room-1"), timeout=1)
        failed, admitted = start_gate.built
        assert failed.stopped and not failed.started
        assert held_runtime.executions["room-1"] is admitted
        assert held_runtime.unsettled_rooms == []

    async def test_rejoin_during_held_start_admits_a_fresh_execution(
        self, held_runtime: AgentRuntime, start_gate: StartGate
    ):
        start_gate.hold()
        joining = asyncio.create_task(held_runtime._on_room_joined("room-1", {}))
        await start_gate.entered.wait()
        await held_runtime._on_room_left("room-1")
        assert await held_runtime.rooms.admit("room-1") is None

        start_gate.release.set()
        await joining
        await asyncio.wait_for(until_admitted(held_runtime, "room-1"), timeout=1)
        withdrawn, admitted = start_gate.built
        assert withdrawn.stopped
        assert held_runtime.executions["room-1"] is admitted

    async def test_leave_behind_a_held_start_is_consumed_and_wins(
        self, mock_link, mock_handler, room_cleanup: RoomCleanup, start_gate: StartGate
    ):
        feed = QueueLink(mock_link)
        runtime = AgentRuntime(
            mock_link,
            "agent-123",
            mock_handler,
            execution_factory=start_gate,
            on_session_cleanup=room_cleanup,
        )
        await runtime.start()
        try:
            start_gate.hold()
            feed.events.put_nowait(make_room_added_event("room-1"))
            await start_gate.entered.wait()
            feed.events.put_nowait(make_room_removed_event("room-1"))
            await wait_for_condition(lambda: not runtime.rooms.wanted("room-1"))

            start_gate.release.set()
            await settled(runtime, "room-1")
            (execution,) = start_gate.built
            assert execution.started and execution.stopped
            assert runtime.active_sessions == {}
            assert room_cleanup.rooms == ["room-1"]
        finally:
            start_gate.release.set()
            await runtime.stop()
