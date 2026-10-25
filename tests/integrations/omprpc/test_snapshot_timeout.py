"""A missing native snapshot response expires at its request boundary."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpCompletionTimeout, OmpProcessError
from band.integrations.omprpc.custody import process_is_alive
from band.integrations.ompwire import OmpCommandName, OmpEventType
from tests.integrations.omprpc.completion import (
    COMPLETION_BUDGET_S,
    CompletionContext,
    state_requests_observed,
)
from tests.integrations.omprpc.events import EventRecorder
from tests.integrations.ompscenarios import PeerScenario
from tests.toolkit.clock import binary_clock_resolution


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestSnapshotTimeout:
    @pytest.mark.parametrize(
        "completion_turn", [PeerScenario.GET_STATE_SILENT], indirect=True
    )
    async def test_lost_snapshot_response_retires_the_native_process(
        self, completion_turn: CompletionContext[EventRecorder]
    ) -> None:
        async with completion_turn as turn:
            await turn.observer.wait_for(OmpEventType.AGENT_END)
            await state_requests_observed(turn.client)
            assert not turn.running.done()

            await turn.clock.elapse(COMPLETION_BUDGET_S)
            with pytest.raises(
                OmpCompletionTimeout, match="get_state response timed out"
            ):
                await turn.clock.run_io(turn.running)
            with pytest.raises(OmpProcessError):
                await turn.client.request(OmpCommandName.GET_STATE)
            assert not await asyncio.to_thread(process_is_alive, turn.peer)
