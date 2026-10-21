"""STOP and INTERRUPT end a batch turn parked on an approval and free the room."""

from __future__ import annotations

import pytest

from band.client.streaming import ControlMode
from tests.adapters.codexbatch.kit import (
    ROOM_ID,
    OpenRoom,
    approval_request,
    reply_to_room,
)
from tests.adapters.codexturns import turn_completed


class TestBatchInterrupts:
    @pytest.mark.parametrize("mode", [ControlMode.STOP, ControlMode.INTERRUPT])
    async def test_the_parked_turn_ends_and_the_room_accepts_the_next_batch(
        self, codex_room: OpenRoom, mode: ControlMode
    ) -> None:
        room = await codex_room(
            approval_request(10),
            turn_completed(),
            reply_to_room(),
            turn_completed("turn-2"),
        )
        await room.send_batch(*room.alice_helper_alice())

        await room.adapter.on_interrupt(ROOM_ID, mode)
        tools = await room.send_batch(*room.alice_helper_alice())

        assert (tools.turn.replied, await room.pending_approvals()) == (
            True,
            "No pending approvals.",
        )
