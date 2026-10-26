"""Background work cannot spend an idle completion budget."""

from __future__ import annotations

import pytest

from band.integrations.ompwire import OmpEventType
from tests.integrations.omppeers.base import BACKGROUND_COMPLETE, YIELDED
from tests.integrations.omprpc.completion import (
    BACKGROUND_WINDOW_S,
    CompletionContext,
    background_claim_observed,
    snapshots_observed,
)
from tests.integrations.omprpc.events import EventRecorder
from tests.integrations.ompscenarios import PeerCommand, PeerScenario
from tests.toolkit.clock import binary_clock_resolution


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestBackgroundCompletion:
    @pytest.mark.parametrize(
        "completion_turn", [PeerScenario.SETTLED_WITH_PENDING], indirect=True
    )
    async def test_pending_work_overrules_a_contradictory_settlement(
        self, completion_turn: CompletionContext[EventRecorder]
    ) -> None:
        async with completion_turn as turn:
            await turn.observer.wait_for(OmpEventType.AGENT_END)
            await snapshots_observed(turn.client)
            await turn.clock.elapse(BACKGROUND_WINDOW_S)
            assert not turn.running.done()

            await turn.client.request(PeerCommand.RELEASE_PROMPT)
            await turn.clock.run_io(turn.running)
            assert BACKGROUND_COMPLETE in turn.observer.assistant_texts

    @pytest.mark.parametrize(
        "completion_turn", [PeerScenario.SILENT_SETTLED_CONTINUATION], indirect=True
    )
    async def test_a_fresh_snapshot_can_finish_background_work_without_a_settlement_frame(
        self, completion_turn: CompletionContext[EventRecorder]
    ) -> None:
        async with completion_turn as turn:
            await background_claim_observed(turn.observer)
            await snapshots_observed(turn.client)
            await turn.clock.elapse(BACKGROUND_WINDOW_S)
            assert not turn.running.done()

            await turn.client.request(PeerCommand.RELEASE_PROMPT)
            await turn.clock.run_io(turn.running)
            assert BACKGROUND_COMPLETE in turn.observer.assistant_texts

    @pytest.mark.parametrize(
        "completion_turn", [PeerScenario.YIELD_TO_BACKGROUND], indirect=True
    )
    async def test_claimed_work_outlives_the_idle_budget_and_delivers_its_continuation(
        self, completion_turn: CompletionContext[EventRecorder]
    ) -> None:
        async with completion_turn as turn:
            await background_claim_observed(turn.observer)
            await snapshots_observed(turn.client)
            await turn.clock.elapse(BACKGROUND_WINDOW_S)
            assert not turn.running.done()

            await turn.client.request(PeerCommand.RELEASE_PROMPT)
            await turn.clock.run_io(turn.running)
            assert turn.observer.assistant_texts == [YIELDED, BACKGROUND_COMPLETE]
