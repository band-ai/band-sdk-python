"""A failed write recovers before the next configured execution attempt."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.platform.settlement.kit import ROOM_ID
from tests.runtime.attempts.conftest import EXECUTION_BUDGET
from tests.runtime.recovery.kit import (
    CONTROL,
    RESYNC_SECONDS,
    Ending,
    Entrance,
    Fault,
    Harness,
    Route,
)

pytestmark = pytest.mark.looptime


@pytest.mark.parametrize("fault", list(Fault))
@pytest.mark.parametrize("replace", [False, True], ids=["original", "replacement"])
class TestFailureContinuation:
    @pytest.mark.parametrize("route", list(Route))
    async def test_an_unknown_failed_write_preserves_the_execution_budget(
        self, retrying_room: Harness, fault: Fault, replace: bool, route: Route
    ) -> None:
        room = retrying_room
        room.peer.fail_terminals(fault, *room.members)
        await room.start(route)
        await room.advance(RESYNC_SECONDS)
        if replace:
            await room.replace_room()

        room.peer.post(CONTROL)
        await room.advance(RESYNC_SECONDS)

        assert room.model_turns == [room.members]
        assert room.adapter.controls == [CONTROL]

        room.heal()
        await room.recover(Entrance.RECONCILIATION)

        assert room.model_turns == [room.members] * EXECUTION_BUDGET
        assert room.member_statuses == dict.fromkeys(
            room.members, DeliveryStatus.FAILED
        )
        assert room.ctx.claims.pending_ack_ids(ROOM_ID) == []
        assert len(room.peer.reports) == 1

    async def test_a_confirmed_failure_allows_a_later_successful_attempt(
        self, retrying_room: Harness, fault: Fault, replace: bool
    ) -> None:
        room = retrying_room
        room.peer.fail_terminals(fault, *room.members)
        await room.start(Route.BACKLOG)
        if replace:
            await room.replace_room()
        assert room.model_turns == [room.members]

        room.adapter.ending = Ending.DECLINES
        room.heal()
        await room.recover(Entrance.RECONCILIATION)

        assert room.model_turns == [room.members, room.members]
        assert room.member_statuses == dict.fromkeys(
            room.members, DeliveryStatus.PROCESSED
        )
        assert room.ctx.claims.pending_ack_ids(ROOM_ID) == []
