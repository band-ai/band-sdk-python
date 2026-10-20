"""A cancelled caller cannot overlap the native Flow that it left running."""

from __future__ import annotations

import asyncio

import pytest

from band.adapters.crewai_flow import CrewAIFlowAdapter, HistoryCrewAIFlowStateSource
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.crewaiflow.kit import QueuedFlows, deliver, queued_callers
from tests.adapters.waiting import UNTIL_TIMEOUT_S


class TestRoomSerialization:
    async def test_next_turn_waits_for_cancelled_callers_native_flow(self) -> None:
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
                    first.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await first
                    second = tasks.create_task(
                        deliver(adapter, tools, work, work.second_id)
                    )
                    cleanup = tasks.create_task(adapter.on_cleanup(work.room_id))
                    await queued_callers(adapter, work.room_id, count=2)
                    assert work.calls == [work.first_id]
                    work.first.released.set()
                    await work.second.arrived()
                    work.second.released.set()
                    await second
                    await cleanup
                    assert work.calls == [work.first_id, work.second_id]
                    assert work.peak_concurrency == 1
                finally:
                    work.first.released.set()
                    work.second.released.set()
        finally:
            await adapter.cleanup_all()
