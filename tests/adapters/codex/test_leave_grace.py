"""A leaving room's turn gets its settle grace with admission closed: what
it still asks is declined instead of parking on a human."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from band.adapters.codex import CodexApprovalMethod
from band.testing import failure_reports
from tests.adapters.codex.kit import ROOM_ID, CodexRoom, parked_on_approval
from tests.adapters.codexturns import event_request, turn_completed

DECLINED = {"decision": "decline"}


class TestLeaveGrace:
    async def test_an_approval_asked_during_a_leave_is_declined_and_the_turn_ends(
        self, codex_room: Callable[..., Awaitable[CodexRoom]]
    ) -> None:
        room = await codex_room(
            *parked_on_approval(
                event_request(
                    11, CodexApprovalMethod.COMMAND_EXECUTION, {"command": "b"}
                ),
                turn_completed(),
            )
        )
        await room.send("run tests")

        await room.adapter.on_cleanup(ROOM_ID)

        assert room.client.responses == [(10, DECLINED), (11, DECLINED)]
        [turn] = room.deliveries
        assert failure_reports(turn) == []
