"""Completion owns its deadline even before its state request reaches stdin."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpCompletionTimeout, OmpProcessError
from band.integrations.omprpc.custody import process_is_alive
from band.integrations.ompwire import OMP_FRAME_BYTES, OmpEventType
from tests.integrations.omppeers.pressure import PRESSURE_READY, PRESSURE_RELEASE
from tests.integrations.omprpc.backpressure import (
    PRESSURE_WRITE_COUNT,
    BackpressureSpawn,
)
from tests.integrations.omprpc.completion import COMPLETION_BUDGET_S, CompletionContext
from tests.integrations.omprpc.events import EventRecorder
from tests.integrations.omprpc.gate import ProcessGate
from tests.integrations.ompscenarios import PeerCommand
from tests.toolkit.clock import binary_clock_resolution


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestCompletionBackpressure:
    async def test_state_request_deadline_includes_waiting_to_write(
        self,
        pressure_completion_turn: CompletionContext[EventRecorder],
        pressure_spawn: BackpressureSpawn,
        process_gate: ProcessGate,
    ) -> None:
        async with pressure_completion_turn as turn:
            writes = []
            try:
                await turn.observer.wait_for(OmpEventType.AGENT_START)
                await turn.client.request(PeerCommand.HOLD, port=process_gate.port)
                reader, writer = await turn.clock.run_io(process_gate.connections.get())
                assert await turn.clock.run_io(reader.readline()) == PRESSURE_READY

                writes = [
                    asyncio.create_task(
                        turn.client.request(
                            PeerCommand.PRESSURE, data="x" * (OMP_FRAME_BYTES // 2)
                        )
                    )
                    for _ in range(PRESSURE_WRITE_COUNT)
                ]
                await turn.clock.run_io(pressure_spawn.paused.wait())
                writer.write(PRESSURE_RELEASE)
                await writer.drain()
                await turn.observer.wait_for(OmpEventType.AGENT_END)

                await turn.clock.elapse(COMPLETION_BUDGET_S)
                with pytest.raises(OmpCompletionTimeout):
                    await turn.clock.run_io(turn.running)
                assert not await asyncio.to_thread(process_is_alive, turn.peer)
                with pytest.raises(OmpProcessError):
                    await turn.client.request(PeerCommand.CHECKPOINT)
            finally:
                for writing in writes:
                    writing.cancel()
                await turn.clock.run_io(asyncio.gather(*writes, return_exceptions=True))
