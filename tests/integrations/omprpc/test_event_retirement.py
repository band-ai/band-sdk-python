"""Event retirement retains custody and discards stale delivery before recovery."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpProcessError, OmpRpcError
from band.integrations.omprpc.custody import process_is_alive
from band.integrations.ompwire import OmpCommandName, OmpEventType
from tests.integrations.omppeers.base import COMPLETE
from tests.integrations.omprpc.callbacks import HeldEventFactory
from tests.integrations.omprpc.events import follow_up_texts
from tests.integrations.omprpc.processes import peer_process
from tests.integrations.ompscenarios import PeerCommand, PeerScenario


class TestEventCallbackRetirement:
    async def test_cancelled_turn_keeps_ownership_until_its_callback_retires(
        self, held_event_turn: HeldEventFactory
    ) -> None:
        async with held_event_turn(PeerScenario.EVENT_BACKPRESSURE) as turn:
            await turn.observer.entered.wait()
            turn.running.cancel()
            await turn.observer.cancel_seen.wait()

            with pytest.raises(OmpProcessError):
                await turn.client.request(OmpCommandName.GET_STATE)
            with pytest.raises(asyncio.CancelledError):
                await turn.running
            assert turn.process.returncode is not None
            assert not await asyncio.to_thread(process_is_alive, turn.peer)
            assert not turn.observer.finished.is_set()
            with pytest.raises(OmpProcessError):
                await turn.client.start()
            with pytest.raises(OmpProcessError):
                await turn.client.close()
            with pytest.raises(OmpProcessError):
                await turn.client.start()

    @pytest.mark.parametrize("queued_event_count", [0, 1], ids=["empty", "backlog"])
    async def test_cancellation_release_discards_stale_events_before_follow_up(
        self, held_event_turn: HeldEventFactory, queued_event_count: int
    ) -> None:
        async with held_event_turn(PeerScenario.EVENT_BACKPRESSURE) as turn:
            await turn.observer.entered.wait()
            # The response follows the burst, so the reader has admitted its events.
            await turn.client.request(PeerCommand.EVENT_BURST, count=queued_event_count)
            turn.running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await turn.running

            await turn.observer.release()
            await turn.observer.retired.wait()
            await turn.client.close()
            await turn.client.start()

            assert await peer_process(turn.client) != turn.peer
            assert await follow_up_texts(turn.client) == [COMPLETE]
            assert turn.observer.outline == [OmpEventType.AGENT_START]

    async def test_rejected_prompt_recovers_after_its_surviving_callback_retires(
        self, held_event_turn: HeldEventFactory
    ) -> None:
        async with held_event_turn(PeerScenario.LATE_PROMPT_ERROR) as turn:
            await turn.observer.entered.wait()
            await turn.client.request(PeerCommand.RELEASE_PROMPT)
            with pytest.raises(OmpRpcError):
                await turn.running

            assert turn.observer.cancel_seen.is_set()
            assert not turn.observer.finished.is_set()
            with pytest.raises(OmpProcessError):
                await turn.client.close()
            assert turn.process.returncode is not None
            assert not await asyncio.to_thread(process_is_alive, turn.peer)
            with pytest.raises(OmpProcessError):
                await turn.client.start()

            await turn.observer.release()
            await turn.observer.retired.wait()
            await turn.client.close()
            await turn.client.start()

            assert await peer_process(turn.client) != turn.peer
            assert await follow_up_texts(turn.client) == [COMPLETE]
