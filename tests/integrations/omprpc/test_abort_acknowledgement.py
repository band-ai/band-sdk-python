"""Stop-after-tool completion includes the required abort acknowledgement."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpCompletionTimeout, OmpProcessError
from band.integrations.omprpc.custody import process_is_alive
from band.integrations.ompwire import OmpEventType
from tests.integrations.omppeers.base import COMPLETE
from tests.integrations.omprpc.acknowledgements import StoppingObserver
from tests.integrations.omprpc.completion import (
    COMPLETION_BUDGET_S,
    CompletionContext,
    snapshots_observed,
)
from tests.integrations.omprpc.events import follow_up_texts
from tests.integrations.omprpc.sessions import RpcTurn
from tests.integrations.ompscenarios import PeerCommand
from tests.toolkit.clock import binary_clock_resolution


class TestAbortAcknowledgement:
    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_lost_abort_response_retires_completed_native_turn(
        self, stopping_completion_turn: CompletionContext[StoppingObserver]
    ) -> None:
        async with stopping_completion_turn as turn:
            await turn.observer.wait_for(OmpEventType.AGENT_END)
            await snapshots_observed(turn.client)
            await turn.clock.elapse(COMPLETION_BUDGET_S)

            with pytest.raises(OmpCompletionTimeout):
                await turn.clock.run_io(turn.running)
            assert not await asyncio.to_thread(process_is_alive, turn.peer)
            with pytest.raises(OmpProcessError):
                await turn.client.request(PeerCommand.CHECKPOINT)
            await turn.clock.run_io(turn.client.start())
            assert await turn.clock.run_io(follow_up_texts(turn.client)) == [COMPLETE]

    async def test_delayed_abort_response_completes_the_stopped_turn(
        self, stopping_turn: RpcTurn[StoppingObserver]
    ) -> None:
        await stopping_turn.observer.wait_for(OmpEventType.AGENT_END)
        await snapshots_observed(stopping_turn.client)
        assert not stopping_turn.running.done()

        await stopping_turn.client.request(PeerCommand.RELEASE_ACK)
        await stopping_turn.running
        assert stopping_turn.observer.assistant_texts == [COMPLETE]
