"""Every native busy signal defeats an otherwise settled snapshot."""

from __future__ import annotations

import pytest

from band.integrations.ompwire import OmpEventType
from tests.integrations.omppeers.base import COMPLETE
from tests.integrations.omppeers.idle import BUSY_FIELDS
from tests.integrations.omprpc.completion import (
    BACKGROUND_WINDOW_S,
    CompletionContext,
    snapshots_observed,
)
from tests.integrations.omprpc.events import EventRecorder
from tests.integrations.ompscenarios import PeerCommand
from tests.toolkit.clock import binary_clock_resolution


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestBusyCompletion:
    @pytest.mark.parametrize("completion_turn", BUSY_FIELDS, indirect=True)
    async def test_busy_snapshot_waits_for_fresh_idle_evidence(
        self, completion_turn: CompletionContext[EventRecorder]
    ) -> None:
        async with completion_turn as turn:
            await turn.observer.wait_for(OmpEventType.AGENT_END)
            await snapshots_observed(turn.client)

            await turn.clock.elapse(BACKGROUND_WINDOW_S)
            assert not turn.running.done()

            await turn.client.request(PeerCommand.RELEASE_PROMPT)
            await turn.clock.run_io(turn.running)
            assert turn.observer.assistant_texts == [COMPLETE, COMPLETE]
