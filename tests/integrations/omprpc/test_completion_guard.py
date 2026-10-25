"""Idle completion expires without inventing missing native evidence."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpCompletionTimeout, OmpProcessError
from band.integrations.omprpc.custody import process_is_alive
from band.integrations.ompwire import OmpCommandName, OmpEventType
from tests.integrations.omppeers.base import COMPLETE
from tests.integrations.omprpc.completion import (
    BACKGROUND_WINDOW_S,
    COMPLETION_BUDGET_S,
    CompletionContext,
    background_claim_observed,
    snapshots_observed,
)
from tests.integrations.omprpc.events import EventRecorder, follow_up_texts
from tests.integrations.ompscenarios import PeerCommand, PeerScenario
from tests.toolkit.clock import binary_clock_resolution


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestCompletionGuard:
    @pytest.mark.parametrize(
        "completion_turn",
        [
            PeerScenario.NO_RESULT,
            PeerScenario.STALE_IDLE_RESULT,
            PeerScenario.LOST_SETTLEMENT,
        ],
        indirect=True,
    )
    async def test_idle_failure_retires_before_a_fresh_turn(
        self, completion_turn: CompletionContext[EventRecorder]
    ) -> None:
        async with completion_turn as turn:
            await turn.observer.wait_for(OmpEventType.AGENT_END)
            await snapshots_observed(turn.client)
            assert not turn.running.done()

            await turn.clock.elapse(COMPLETION_BUDGET_S)
            with pytest.raises(OmpCompletionTimeout, match="completion stayed idle"):
                await turn.clock.run_io(turn.running)
            assert not await asyncio.to_thread(process_is_alive, turn.peer)
            with pytest.raises(OmpProcessError):
                await turn.client.request(OmpCommandName.GET_STATE)

            await turn.clock.run_io(turn.client.start())
            assert await turn.clock.run_io(follow_up_texts(turn.client)) == [COMPLETE]

    @pytest.mark.parametrize(
        "completion_turn", [PeerScenario.PENDING_NO_RESULT], indirect=True
    )
    async def test_missing_result_budget_starts_after_background_settles(
        self, completion_turn: CompletionContext[EventRecorder]
    ) -> None:
        async with completion_turn as turn:
            await background_claim_observed(turn.observer)
            await turn.clock.elapse(BACKGROUND_WINDOW_S)
            assert not turn.running.done()

            await turn.client.request(PeerCommand.RELEASE_PROMPT)
            await snapshots_observed(turn.client)
            await turn.clock.elapse(COMPLETION_BUDGET_S)
            with pytest.raises(OmpCompletionTimeout, match="completion stayed idle"):
                await turn.clock.run_io(turn.running)
            assert not await asyncio.to_thread(process_is_alive, turn.peer)
            await turn.clock.run_io(turn.client.start())
            assert await turn.clock.run_io(follow_up_texts(turn.client)) == [COMPLETE]
