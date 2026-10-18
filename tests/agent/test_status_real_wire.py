"""Host-visible snapshots of current lifecycle and room ownership."""

from __future__ import annotations

import asyncio
from functools import partial
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

import band
from band.core.exceptions import CleanupIncompleteError
from band.runtime.execution import ExecutionState
from band.runtime.rooms import RoomState
from band.runtime.runtime import AgentRuntime
from band.testing import fake_phoenix_server
from tests.agent.kit import MinimalAdapter
from tests.agent.wirekit import SUPERSEDE, wire_agent
from tests.runtime.conftest import platform_msg, wait_for_condition
from tests.runtime.helpers import RoomCleanup, StartGate

ROOM_EVENT = {
    "id": "room-1",
    "inserted_at": "2026-09-26T00:00:00Z",
    "updated_at": "2026-09-26T00:00:00Z",
}


class TestAgentStatus:
    async def test_snapshot_tracks_processing_membership_and_stays_immutable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def on_event(*args: object, **kwargs: object) -> None:
            entered.set()
            await release.wait()

        adapter = MinimalAdapter()
        adapter.on_event = AsyncMock(side_effect=on_event)
        async with fake_phoenix_server() as server:
            agent = wire_agent(
                server.url,
                monkeypatch,
                adapter=adapter,
                next_messages=[platform_msg("msg-1")],
            )
            before = agent.status()
            assert not before.running and not before.connected
            assert not before.cleanup_pending
            assert before.started_at is None and before.rooms == ()
            await agent.start()
            try:
                await asyncio.wait_for(entered.wait(), timeout=2.0)
                processing = agent.status()
                assert isinstance(processing, band.AgentStatus)
                assert processing.running and processing.connected
                assert processing.started_at is not None
                assert not processing.cleanup_pending
                assert len(processing.rooms) == 1
                room = processing.rooms[0]
                assert isinstance(room, band.RoomStatus)
                assert room.room_id == "room-1"
                assert room.state is ExecutionState.PROCESSING
                assert room.lifecycle is RoomState.SERVING
                assert room.member and room.routed and not room.unsettled
                with pytest.raises(ValidationError):
                    processing.connected = False
                with pytest.raises(ValidationError):
                    room.routed = False

                release.set()
                await wait_for_condition(
                    lambda: agent.status().rooms[0].state is ExecutionState.IDLE
                )
                await server.push("agent_rooms:agent-123", "room_removed", ROOM_EVENT)
                await wait_for_condition(lambda: agent.status().rooms == ())
            finally:
                release.set()
                await agent.stop()
            stopped = agent.status()
            assert not stopped.running and not stopped.connected
            assert stopped.started_at is None and stopped.rooms == ()
            assert not stopped.cleanup_pending
            assert processing.rooms[0].state is ExecutionState.PROCESSING
            assert processing.rooms[0].member and processing.connected

    async def test_ordinary_transport_loss_and_reconnect_preserve_the_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch)
            await agent.runtime.initialize()
            link = agent.runtime.link
            disconnected = asyncio.Event()
            observations = []
            original = link._on_disconnected

            async def observe_disconnect(error: Exception | None) -> None:
                await original(error)
                observations.append(agent.status())
                disconnected.set()

            monkeypatch.setattr(link, "_on_disconnected", observe_disconnect)
            await agent.start()
            await wait_for_condition(
                lambda: "room-1" in agent.runtime.runtime.executions
            )
            running = asyncio.create_task(
                agent.run_forever(install_signal_handlers=False)
            )
            try:
                first = agent.status()
                await server.abort_connection()
                await asyncio.wait_for(disconnected.wait(), timeout=2.0)
                lost = observations[0]
                assert not lost.connected and lost.running
                assert not lost.cleanup_pending and lost.last_disconnect_reason is None
                assert lost.started_at == first.started_at
                assert [room.room_id for room in lost.rooms] == ["room-1"]
                await wait_for_condition(lambda: agent.status().connected, timeout=10.0)
                reconnected = agent.status()
                assert reconnected.running and not running.done()
                assert reconnected.started_at == first.started_at
                assert reconnected.last_disconnect_reason is None
                assert server.connection_count == 2
            finally:
                await agent.stop()
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
            assert not lost.connected

    async def test_room_inventory_includes_starting_and_retiring_custom_execution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gate = StartGate()
        gate.hold()
        cleanup = RoomCleanup()
        adapter = MinimalAdapter()
        adapter.on_cleanup = cleanup
        monkeypatch.setattr(
            "band.runtime.platform_runtime.AgentRuntime",
            partial(AgentRuntime, execution_factory=gate),
        )
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch, adapter=adapter)
            await agent.start()
            try:
                await asyncio.wait_for(gate.entered.wait(), timeout=2.0)
                starting = agent.status().rooms[0]
                assert starting.room_id == "room-1" and starting.member
                assert starting.lifecycle is RoomState.STARTING
                assert starting.state is None
                assert starting.unsettled and not starting.routed
                gate.release.set()
                await wait_for_condition(lambda: agent.status().rooms[0].routed)
                serving = agent.status().rooms[0]
                assert serving.member and not serving.unsettled
                assert serving.lifecycle is RoomState.SERVING and serving.state is None

                cleanup.hold()
                await server.push("agent_rooms:agent-123", "room_removed", ROOM_EVENT)
                await asyncio.wait_for(cleanup.entered.wait(), timeout=2.0)
                retiring = agent.status().rooms[0]
                assert retiring.room_id == "room-1" and not retiring.member
                assert retiring.lifecycle is RoomState.RETIRING
                assert retiring.unsettled and not retiring.routed
                cleanup.release.set()
                await wait_for_condition(lambda: agent.status().rooms == ())
                assert starting.lifecycle is RoomState.STARTING and starting.member
            finally:
                gate.release.set()
                cleanup.release.set()
                await agent.stop()

    async def test_failed_room_cleanup_stays_visible_and_blocks_restart(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cleanup = RoomCleanup()
        adapter = MinimalAdapter()
        adapter.on_cleanup = cleanup
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch, adapter=adapter)
            await agent.start()
            await wait_for_condition(
                lambda: "room-1" in agent.runtime.runtime.executions
            )
            cleanup.failing = True
            try:
                first = agent.status()
                with pytest.raises(ExceptionGroup):
                    await agent.stop()
                failed = agent.status()
                assert not failed.running and not failed.connected
                assert failed.started_at is None and failed.cleanup_pending
                assert len(failed.rooms) == 1
                room = failed.rooms[0]
                assert room.room_id == "room-1"
                assert not room.member and not room.routed and room.unsettled
                assert room.lifecycle is RoomState.IDLE
                assert agent.runtime.status().cleanup_pending
                with pytest.raises(CleanupIncompleteError):
                    await agent.start()
            finally:
                cleanup.failing = False
                await agent.stop()
            assert not agent.status().cleanup_pending and agent.status().rooms == ()
            await agent.start()
            try:
                restarted = agent.status()
                assert restarted.running and restarted.connected
                assert first.started_at is not None and restarted.started_at is not None
                assert restarted.started_at > first.started_at
                assert failed.rooms[0].unsettled
            finally:
                await agent.stop()

    async def test_cancelled_stop_keeps_cleanup_and_room_custody_visible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cleanup = RoomCleanup()
        adapter = MinimalAdapter()
        adapter.on_cleanup = cleanup
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch, adapter=adapter)
            await agent.start()
            await wait_for_condition(
                lambda: "room-1" in agent.runtime.runtime.executions
            )
            cleanup.hold()
            stopping = asyncio.create_task(agent.stop())
            try:
                await asyncio.wait_for(cleanup.entered.wait(), timeout=2.0)
                draining = agent.status()
                assert not draining.running and draining.cleanup_pending
                assert draining.started_at is None
                assert draining.rooms[0].lifecycle is RoomState.RETIRING
                stopping.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await stopping
                cancelled = agent.status()
                assert not cancelled.running and not cancelled.connected
                assert cancelled.cleanup_pending and cancelled.started_at is None
                assert cancelled.rooms[0].unsettled
                with pytest.raises(CleanupIncompleteError):
                    await agent.start()
            finally:
                cleanup.release.set()
                await asyncio.gather(stopping, return_exceptions=True)
                await agent.stop()
            assert not agent.status().cleanup_pending and agent.status().rooms == ()

    async def test_adapter_only_cleanup_failure_is_not_a_healthy_stopped_agent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = MinimalAdapter()
        adapter.cleanup_all = AsyncMock(
            side_effect=OSError("adapter still owns a client")
        )
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch, adapter=adapter, room_ids=())
            await agent.start()
            try:
                with pytest.raises(ExceptionGroup):
                    await agent.stop()
                status = agent.status()
                assert status.cleanup_pending and not status.running
                assert not status.connected and status.started_at is None
                assert status.rooms == ()
                assert not agent.runtime.status().cleanup_pending
            finally:
                adapter.cleanup_all.side_effect = None
                await agent.stop()
            assert not agent.status().cleanup_pending

    async def test_terminal_reason_survives_stop_but_not_restart(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch)
            await agent.start()
            try:
                first = agent.status()
                await server.push("agent_control:agent-123", "supersede", SUPERSEDE)
                await wait_for_condition(
                    lambda: agent.status().last_disconnect_reason is not None
                )
                terminal = agent.status()
                assert not terminal.connected and not terminal.running
                assert terminal.cleanup_pending and terminal.started_at is None
                assert terminal.last_disconnect_reason is agent.last_disconnect_reason
            finally:
                await agent.stop()
            stopped = agent.status()
            assert stopped.last_disconnect_reason is terminal.last_disconnect_reason
            assert not stopped.cleanup_pending and stopped.rooms == ()
            await agent.start()
            try:
                restarted = agent.status()
                assert restarted.last_disconnect_reason is None
                assert restarted.connected and restarted.running
                assert first.started_at is not None and restarted.started_at is not None
                assert restarted.started_at > first.started_at
                assert (
                    terminal.last_disconnect_reason.reason
                    == "session.already_connected"
                )
            finally:
                await agent.stop()

    async def test_membership_remains_visible_without_a_routed_or_owned_execution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch, room_ids=("room-b", "room-a"))
            await agent.start()
            try:
                await wait_for_condition(
                    lambda: len(agent.runtime.runtime.executions) == 2
                )
                await agent.runtime.runtime.rooms.destroy("room-b")
                status = agent.status()
                assert [room.room_id for room in status.rooms] == ["room-a", "room-b"]
                membership_only = status.rooms[1]
                assert membership_only.member
                assert not membership_only.routed and not membership_only.unsettled
                assert membership_only.lifecycle is None
                assert membership_only.state is None
            finally:
                await agent.stop()

    async def test_failed_start_does_not_publish_a_success_timestamp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = MinimalAdapter()
        adapter.on_started = AsyncMock(side_effect=OSError("startup refused"))
        async with fake_phoenix_server() as server:
            agent = wire_agent(server.url, monkeypatch, adapter=adapter)
            with pytest.raises(OSError, match="startup refused"):
                await agent.start()
            status = agent.status()
            assert not status.running and not status.connected
            assert not status.cleanup_pending
            assert status.started_at is None and status.rooms == ()
