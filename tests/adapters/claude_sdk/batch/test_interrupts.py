"""STOP and INTERRUPT end a batch turn parked on an approval and free the room."""

from __future__ import annotations

import pytest

from band.client.streaming import ControlMode
from tests.adapters.claude_sdk.batch.kit import LIST_FILES, OpenRoom, alice_helper_alice
from tests.adapters.claude_sdk.helpers import with_approvals


class TestBatchInterrupts:
    @pytest.mark.parametrize("mode", [ControlMode.STOP, ControlMode.INTERRUPT])
    async def test_the_parked_turn_ends_and_the_room_accepts_the_next_batch(
        self, claude_room: OpenRoom, mode: ControlMode
    ) -> None:
        room = await claude_room(with_approvals())
        room.claude.script([LIST_FILES, room.model_reply("Never sent.")])
        await room.send_batch(*alice_helper_alice(room))

        await room.adapter.on_interrupt(room.room_id, mode)
        room.claude.script([room.model_reply("Fresh start.")])
        await room.send_batch(*alice_helper_alice(room))
        await room.settled()

        assert "Never sent." not in room.chat
        assert room.chat[-1] == "Fresh start."
        assert room.adapter._pending_approvals == {}
