"""A missing admission response stays bounded after native completion."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpCompletionTimeout, OmpProcessError
from band.integrations.omprpc.custody import process_is_alive
from band.integrations.ompwire import OmpEventType
from tests.integrations.omppeers.base import COMPLETE
from tests.integrations.omprpc.completion import (
    COMPLETION_BUDGET_S,
    CompletionContext,
    snapshots_observed,
)
from tests.integrations.omprpc.events import EventRecorder, follow_up_texts
from tests.integrations.omprpc.sessions import RpcTurn
from tests.integrations.ompscenarios import PeerCommand, PeerScenario
from tests.toolkit.clock import binary_clock_resolution


class TestPromptAcknowledgement:
    @pytest.mark.looptime(resolution=binary_clock_resolution())
    @pytest.mark.parametrize(
        "completion_turn", [PeerScenario.LOST_PROMPT_ACK], indirect=True
    )
    async def test_lost_admission_response_retires_completed_native_turn(
        self, completion_turn: CompletionContext[EventRecorder]
    ) -> None:
        async with completion_turn as turn:
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

    @pytest.mark.parametrize("rpc_turn", [PeerScenario.LOST_PROMPT_ACK], indirect=True)
    async def test_delayed_admission_response_completes_the_native_turn(
        self, rpc_turn: RpcTurn[EventRecorder]
    ) -> None:
        await rpc_turn.observer.wait_for(OmpEventType.AGENT_END)
        await rpc_turn.client.request(PeerCommand.CHECKPOINT)
        assert not rpc_turn.running.done()

        await rpc_turn.client.request(PeerCommand.RELEASE_ACK)
        await rpc_turn.running
        assert rpc_turn.observer.assistant_texts == [COMPLETE]
