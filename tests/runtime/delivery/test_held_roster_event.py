"""A roster event the room cannot begin yet waits for a later wake instead of being lost."""

from __future__ import annotations

import asyncio
from functools import partial

import pytest

from band.client.streaming import DeliveryStatus
from band.core.turn_lifecycle import CYCLE_CANCEL_GRACE_SECONDS
from band.runtime.cycle import TurnScope
from band.runtime.execution import ExecutionContext
from tests.conftest import make_participant_added_event
from tests.runtime.delivery.kit import ObservedExecution, RestPeer, started_session
from tests.runtime.helpers import ROOM_ID
from tests.toolkit.timecontrol import controlled_clock

ROSTER_TURN = ("participant_added",)
ORIGINAL_MESSAGE_ID = "m1"
ROSTER_PARTICIPANT_ID = "user-456"


class TestHeldRosterEvent:
    @pytest.mark.looptime
    async def test_a_roster_event_waits_out_a_running_stop_observer(
        self, rest_peer: RestPeer
    ) -> None:
        first = rest_peer.post(ORIGINAL_MESSAGE_ID)
        observer_entered = asyncio.Event()
        observer_done = asyncio.Event()
        delivery_started = asyncio.Event()
        delivery_deferred = asyncio.Event()

        async def observe_stop(ctx: ExecutionContext, scope: TurnScope) -> None:
            observer_entered.set()
            await observer_done.wait()

        async with (
            controlled_clock() as clock,
            started_session(
                rest_peer,
                gate=True,
                on_platform_stop=observe_stop,
                context_factory=partial(
                    ObservedExecution,
                    on_local_started=delivery_started.set,
                    on_deferred=delivery_deferred.set,
                ),
            ) as session,
        ):
            try:
                await session.deliver(first)
                await session.turns.entered(ORIGINAL_MESSAGE_ID)
                scope = session.ctx.current_scope
                assert scope is not None
                session.ctx.observe_platform_stop(scope)
                await observer_entered.wait()
                await session.deliver(
                    make_participant_added_event(
                        room_id=ROOM_ID, participant_id=ROSTER_PARTICIPANT_ID
                    )
                )
                # Local admission arms the observer grace before yielding.
                await delivery_started.wait()
                assert not delivery_deferred.is_set()
                clock.advance(CYCLE_CANCEL_GRACE_SECONDS)
                await delivery_deferred.wait()
                assert session.turns.ids == [(ORIGINAL_MESSAGE_ID,)]
            finally:
                observer_done.set()
                session.turns.release.set()
            await session.ctx.request_resync()
            await session.turns.entered(*ROSTER_TURN)
            await rest_peer.write_received(
                DeliveryStatus.PROCESSED, ORIGINAL_MESSAGE_ID
            )

        assert session.turns.ids.count(ROSTER_TURN) == 1
        assert [p["id"] for p in session.ctx.participants] == [ROSTER_PARTICIPANT_ID]
