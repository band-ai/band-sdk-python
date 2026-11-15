"""Idle completion includes event delivery, after background work settles."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpCompletionTimeout
from band.integrations.omprpc.custody import process_is_alive
from tests.integrations.omprpc.callbacks import (
    BlockedEventObserver,
)
from tests.integrations.omprpc.completion import (
    BACKGROUND_WINDOW_S,
    COMPLETION_BUDGET_S,
    CompletionContext,
    snapshot_count,
    snapshots_observed,
)
from tests.integrations.ompscenarios import PeerCommand, PeerScenario
from tests.toolkit.clock import binary_clock_resolution


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestCompletionDrain:
    @pytest.mark.parametrize(
        "blocked_completion_turn", [PeerScenario.DRAIN_BLOCKED_EVENT], indirect=True
    )
    async def test_settlement_cannot_hide_a_stalled_event_drain(
        self, blocked_completion_turn: CompletionContext[BlockedEventObserver]
    ) -> None:
        async with blocked_completion_turn as turn:
            await turn.observer.entered.wait()
            await snapshots_observed(turn.client)
            await turn.clock.elapse(COMPLETION_BUDGET_S)

            with pytest.raises(OmpCompletionTimeout, match="completion stayed idle"):
                await turn.clock.run_io(turn.running)
            assert not await asyncio.to_thread(process_is_alive, turn.peer)

    @pytest.mark.parametrize(
        "blocked_completion_turn",
        [PeerScenario.DRAIN_BLOCKED_BACKGROUND],
        indirect=True,
    )
    async def test_event_drain_budget_starts_after_background_settlement(
        self, blocked_completion_turn: CompletionContext[BlockedEventObserver]
    ) -> None:
        async with blocked_completion_turn as turn:
            await turn.observer.entered.wait()
            await snapshots_observed(turn.client)
            await turn.clock.elapse(BACKGROUND_WINDOW_S)
            assert not turn.running.done()

            observed = await snapshot_count(turn.client)
            await turn.client.request(PeerCommand.RELEASE_PROMPT)
            await snapshots_observed(turn.client, count=observed + 1)
            await turn.clock.elapse(COMPLETION_BUDGET_S)
            with pytest.raises(OmpCompletionTimeout, match="completion stayed idle"):
                await turn.clock.run_io(turn.running)
            assert not await asyncio.to_thread(process_is_alive, turn.peer)
