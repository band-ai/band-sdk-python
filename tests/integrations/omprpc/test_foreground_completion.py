"""Initial native inference cannot spend a completion deadline."""

from __future__ import annotations

import pytest

from band.integrations.ompwire import OmpEventType
from tests.integrations.omppeers.base import COMPLETE
from tests.integrations.omprpc.completion import BACKGROUND_WINDOW_S, CompletionContext
from tests.integrations.omprpc.events import EventRecorder, follow_up_texts
from tests.integrations.ompscenarios import PeerCommand, PeerScenario
from tests.toolkit.clock import binary_clock_resolution


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestForegroundCompletion:
    @pytest.mark.parametrize(
        "completion_turn", [PeerScenario.EVENT_BACKPRESSURE], indirect=True
    )
    async def test_admitted_inference_outlives_the_completion_budget(
        self, completion_turn: CompletionContext[EventRecorder]
    ) -> None:
        async with completion_turn as turn:
            await turn.observer.wait_for(OmpEventType.AGENT_START)
            await turn.client.request(PeerCommand.CHECKPOINT)

            await turn.clock.elapse(BACKGROUND_WINDOW_S)
            await turn.clock.run_io(turn.client.request(PeerCommand.CHECKPOINT))
            assert not turn.running.done()

            await turn.client.request(PeerCommand.EVENT_RESULT)
            await turn.clock.run_io(turn.running)
            assert turn.observer.assistant_texts == [COMPLETE]
            assert await turn.clock.run_io(follow_up_texts(turn.client)) == [COMPLETE]
