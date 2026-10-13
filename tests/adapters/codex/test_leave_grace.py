"""A leaving room's or shutting-down adapter's turn gets its settle grace
with admission closed: what it still asks is declined instead of parking on
a human."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.adapters.codex import CodexAdapter, CodexApprovalMethod
from band.testing import failure_reports
from tests.adapters.codex.kit import DECLINED, ROOM_ID, CodexRoom, parked_on_approval
from tests.adapters.codexturns import event_request, turn_completed


async def leave(adapter: CodexAdapter) -> None:
    await adapter.on_cleanup(ROOM_ID)


async def shut_down(adapter: CodexAdapter) -> None:
    await adapter.cleanup_all()


class TestLeaveGrace:
    @pytest.mark.parametrize("closing", [leave, shut_down])
    async def test_an_approval_asked_while_closing_is_declined_and_the_turn_ends(
        self,
        codex_room: Callable[..., Awaitable[CodexRoom]],
        closing: Callable[[CodexAdapter], Awaitable[None]],
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

        await closing(room.adapter)

        assert room.client.responses == [(10, DECLINED), (11, DECLINED)]
        [turn] = room.deliveries
        assert failure_reports(turn) == []
