"""New native activity receives a fresh idle completion budget."""

from __future__ import annotations

import pytest

from band.integrations.omprpc import OmpCompletionTimeout
from band.integrations.ompwire import OmpEventType
from tests.integrations.omprpc.completion import (
    COMPLETION_BUDGET_S,
    CompletionContext,
    snapshot_count,
    snapshots_observed,
)
from tests.integrations.omprpc.events import EventRecorder
from tests.integrations.ompscenarios import PeerCommand, PeerScenario
from tests.toolkit.clock import binary_clock_resolution

FIRST_IDLE_WINDOW_S = COMPLETION_BUDGET_S * 3 / 4
REFRESHED_IDLE_WINDOW_S = COMPLETION_BUDGET_S / 2


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestCompletionRearming:
    @pytest.mark.parametrize(
        "completion_turn", [PeerScenario.REARM_COMPLETION], indirect=True
    )
    async def test_activity_restarts_the_idle_budget(
        self, completion_turn: CompletionContext[EventRecorder]
    ) -> None:
        async with completion_turn as turn:
            await turn.observer.wait_for(OmpEventType.AGENT_END)
            await snapshots_observed(turn.client)
            await turn.clock.elapse(FIRST_IDLE_WINDOW_S)

            observed = await snapshot_count(turn.client)
            await turn.client.request(PeerCommand.RESUME_ACTIVITY)
            await snapshots_observed(turn.client, observed + 1)
            await turn.clock.elapse(REFRESHED_IDLE_WINDOW_S)
            await turn.clock.run_io(turn.client.request(PeerCommand.CHECKPOINT))
            assert not turn.running.done()

            await turn.clock.elapse(REFRESHED_IDLE_WINDOW_S)
            with pytest.raises(OmpCompletionTimeout, match="completion stayed idle"):
                await turn.clock.run_io(turn.running)

    @pytest.mark.parametrize(
        "completion_turn", [PeerScenario.REARM_COMPLETION], indirect=True
    )
    async def test_repeated_settlement_preserves_the_missing_result_deadline(
        self, completion_turn: CompletionContext[EventRecorder]
    ) -> None:
        async with completion_turn as turn:
            await turn.observer.wait_for(OmpEventType.AGENT_END)
            await snapshots_observed(turn.client)
            await turn.clock.elapse(FIRST_IDLE_WINDOW_S)

            observed = await snapshot_count(turn.client)
            await turn.client.request(PeerCommand.REPEAT_SETTLEMENT)
            await snapshots_observed(turn.client, observed + 1)
            await turn.clock.elapse(COMPLETION_BUDGET_S - FIRST_IDLE_WINDOW_S)

            with pytest.raises(OmpCompletionTimeout, match="completion stayed idle"):
                await turn.clock.run_io(turn.running)
