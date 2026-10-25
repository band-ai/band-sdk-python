"""Cancellation preserves stop semantics through incomplete completion evidence."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpProcessError
from band.integrations.omprpc.custody import process_is_alive
from band.integrations.ompwire import OmpCommandName, OmpEventType
from tests.integrations.omppeers.base import COMPLETE
from tests.integrations.omprpc.callbacks import (
    StubbornToolObserver,
)
from tests.integrations.omprpc.completion import (
    NO_CALLBACK_GRACE_S,
    snapshots_observed,
    state_requests_observed,
)
from tests.integrations.omprpc.events import EventRecorder, follow_up_texts
from tests.integrations.omprpc.sessions import RpcTurn
from tests.integrations.ompscenarios import PeerScenario


class TestCompletionCancellation:
    @pytest.mark.parametrize(
        "rpc_turn",
        [PeerScenario.NO_RESULT, PeerScenario.LOST_SETTLEMENT],
        indirect=True,
    )
    async def test_cancellation_while_completion_is_idle_stays_cancellation(
        self, rpc_turn: RpcTurn[EventRecorder]
    ) -> None:
        turn = rpc_turn
        await turn.observer.wait_for(OmpEventType.AGENT_END)
        await snapshots_observed(turn.client)
        turn.running.cancel()

        with pytest.raises(asyncio.CancelledError):
            await turn.running
        with pytest.raises(OmpProcessError):
            await turn.client.request(OmpCommandName.GET_STATE)
        assert not await asyncio.to_thread(process_is_alive, turn.peer)

    @pytest.mark.parametrize("rpc_turn", [PeerScenario.GET_STATE_SILENT], indirect=True)
    async def test_cancellation_interrupts_a_lost_snapshot_response(
        self, rpc_turn: RpcTurn[EventRecorder]
    ) -> None:
        turn = rpc_turn
        await turn.observer.wait_for(OmpEventType.AGENT_END)
        await state_requests_observed(turn.client)
        turn.running.cancel()

        with pytest.raises(asyncio.CancelledError):
            await turn.running
        with pytest.raises(OmpProcessError):
            await turn.client.request(OmpCommandName.GET_STATE)
        assert not await asyncio.to_thread(process_is_alive, turn.peer)

    async def test_cancellation_with_a_survivor_recovers_only_after_callback_release(
        self, tool_turn: RpcTurn[StubbornToolObserver]
    ) -> None:
        turn = tool_turn
        await turn.observer.entered.wait()
        await turn.observer.wait_for(OmpEventType.AGENT_END)
        await snapshots_observed(turn.client)
        turn.client.callback_exit_timeout_s = NO_CALLBACK_GRACE_S
        turn.running.cancel()

        with pytest.raises(asyncio.CancelledError):
            await turn.running
        assert turn.observer.cancel_seen.is_set()
        assert not turn.observer.finished.is_set()
        assert not await asyncio.to_thread(process_is_alive, turn.peer)
        with pytest.raises(OmpProcessError):
            await turn.client.start()

        await turn.observer.release()
        await turn.observer.retired.wait()
        await turn.client.close()
        await turn.client.start()
        assert await follow_up_texts(turn.client) == [COMPLETE]
