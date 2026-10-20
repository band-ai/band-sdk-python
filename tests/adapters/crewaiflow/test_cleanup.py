from __future__ import annotations

import asyncio

import pytest

from band.adapters.crewai_flow import (
    CrewAIFlowAdapter,
    CrewAIFlowAdapterConfig,
    HistoryCrewAIFlowStateSource,
)
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.crewaiflow.kit import (
    QueuedFlows,
    deliver,
    empty_flow,
    queued_callers,
)
from tests.adapters.waiting import UNTIL_TIMEOUT_S


class TestOnCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_clears_state_source_room_cache(self) -> None:
        cleared = []

        class CacheAwareSource(HistoryCrewAIFlowStateSource):
            def clear_room(self, room_id: str, metadata_namespace: str) -> None:
                cleared.append((room_id, metadata_namespace))

        adapter = CrewAIFlowAdapter(
            CrewAIFlowAdapterConfig(metadata_namespace="crewai_flow:test-agent"),
            flow_factory=empty_flow,
            state_source=CacheAwareSource(acknowledge_test_only=True),
        )
        await adapter.on_cleanup("room-A")

        assert cleared == [("room-A", "crewai_flow:test-agent")]

    @pytest.mark.asyncio
    async def test_cleanup_removes_only_target_room(self) -> None:
        adapter = CrewAIFlowAdapter(flow_factory=empty_flow)
        # Prime locks for two rooms.
        room_a = await adapter._acquire_room_lock_entry("room-A")
        await adapter._release_room_lock_entry("room-A", room_a)
        room_b = await adapter._acquire_room_lock_entry("room-B")
        await adapter._release_room_lock_entry("room-B", room_b)
        assert "room-A" in adapter._room_locks
        assert "room-B" in adapter._room_locks

        await adapter.on_cleanup("room-A")
        assert "room-A" not in adapter._room_locks
        assert "room-B" in adapter._room_locks

    async def test_cleanup_waits_for_in_flight_room_turn(self) -> None:
        work = QueuedFlows()
        work.second.released.set()
        adapter = CrewAIFlowAdapter(
            flow_factory=work.factory,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        await adapter.on_started("router", "")
        tools = FakeAgentTools()
        try:
            async with asyncio.timeout(UNTIL_TIMEOUT_S), asyncio.TaskGroup() as tasks:
                try:
                    first = tasks.create_task(
                        deliver(adapter, tools, work, work.first_id)
                    )
                    await work.first.arrived()
                    cleanup = tasks.create_task(adapter.on_cleanup(work.room_id))
                    second = tasks.create_task(
                        deliver(adapter, tools, work, work.second_id)
                    )
                    await queued_callers(adapter, work.room_id, count=3)
                    assert work.calls == [work.first_id]
                    work.first.released.set()
                    await first
                    await cleanup
                    await second
                    assert work.calls == [work.first_id, work.second_id]
                    assert work.peak_concurrency == 1
                finally:
                    work.first.released.set()
                    work.second.released.set()
        finally:
            await adapter.cleanup_all()

    async def test_cleanup_preserves_the_lock_for_queued_turns(self) -> None:
        work = QueuedFlows()
        adapter = CrewAIFlowAdapter(
            flow_factory=work.factory,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        await adapter.on_started("router", "")
        tools = FakeAgentTools()
        try:
            async with asyncio.timeout(UNTIL_TIMEOUT_S), asyncio.TaskGroup() as tasks:
                try:
                    first = tasks.create_task(
                        deliver(adapter, tools, work, work.first_id)
                    )
                    await work.first.arrived()
                    cleanup = tasks.create_task(adapter.on_cleanup(work.room_id))
                    second = tasks.create_task(
                        deliver(adapter, tools, work, work.second_id)
                    )
                    await queued_callers(adapter, work.room_id, count=3)
                    work.first.released.set()
                    await first
                    await cleanup
                    await work.second.arrived()
                    third = tasks.create_task(
                        deliver(adapter, tools, work, work.third_id)
                    )
                    await queued_callers(adapter, work.room_id, count=2)
                    assert work.calls == [work.first_id, work.second_id]
                    work.second.released.set()
                    await second
                    await third
                    assert work.calls == [work.first_id, work.second_id, work.third_id]
                    assert work.peak_concurrency == 1
                finally:
                    work.first.released.set()
                    work.second.released.set()
        finally:
            await adapter.cleanup_all()
