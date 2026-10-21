"""A roster event the room cannot begin yet waits for a later wake instead of being lost."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.cycle import TurnScope
from band.runtime.execution import ExecutionContext
from tests.conftest import make_participant_added_event
from tests.runtime.delivery.kit import RestPeer, started_session
from tests.runtime.helpers import ROOM_ID

pytestmark = pytest.mark.looptime

ROSTER_TURN = ("participant_added",)


class TestHeldRosterEvent:
    async def test_a_roster_event_waits_out_a_running_stop_observer(
        self, rest_peer: RestPeer
    ) -> None:
        first = rest_peer.post("m1")
        observer_done = asyncio.Event()

        async def observe_stop(ctx: ExecutionContext, scope: TurnScope) -> None:
            await observer_done.wait()

        async with started_session(
            rest_peer, gate=True, on_platform_stop=observe_stop
        ) as session:
            await session.deliver(first)
            await session.until(lambda: session.turns.ids == [("m1",)])
            scope = session.ctx.current_scope
            assert scope is not None
            session.ctx.observe_platform_stop(scope)
            await session.quiesce()
            await session.deliver(make_participant_added_event(room_id=ROOM_ID))
            await session.quiesce()

            observer_done.set()
            session.turns.release.set()
            await session.resync()
            await session.settled("m1")

        assert session.turns.ids.count(ROSTER_TURN) == 1
        assert [p["id"] for p in session.ctx.participants] == ["user-456"]
