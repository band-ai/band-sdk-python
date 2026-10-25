"""STOP and INTERRUPT end a batch turn parked on an approval and free the room."""

from __future__ import annotations

from dataclasses import replace

import pytest

from band.client.streaming import ControlMode
from band.core.types import AgentInput, HistoryProvider
from band.runtime.tools import BandTool
from band.runtime.tools.agent import AgentTools
from tests.adapters.codexbatch.kit import (
    ALICE,
    HELPER,
    ROOM_ID,
    OpenRoom,
    approval_request,
)
from tests.adapters.codexturns import tool_call_request, turn_completed
from tests.core.automaticdelivery.kit import AGENT, MembershipPeer
from tests.runtime.helpers import rest_client_over

STALE_REPLY = "This interrupted turn must not reply."
FRESH_REPLY = "The next batch replied."


class TestBatchInterrupts:
    @pytest.mark.parametrize("mode", [ControlMode.STOP, ControlMode.INTERRUPT])
    async def test_the_parked_turn_ends_and_the_room_accepts_the_next_batch(
        self, codex_room: OpenRoom, mode: ControlMode
    ) -> None:
        room = await codex_room(
            approval_request(10),
            tool_call_request(
                11,
                BandTool.SEND_MESSAGE,
                {"content": STALE_REPLY, "mentions": [f"@{ALICE['id']}"]},
            ),
            turn_completed(),
            tool_call_request(
                12,
                BandTool.SEND_MESSAGE,
                {"content": FRESH_REPLY, "mentions": [f"@{ALICE['id']}"]},
            ),
            turn_completed("turn-2"),
        )
        peer = MembershipPeer([ALICE["id"], HELPER["id"], AGENT])
        async with rest_client_over(peer.answer) as rest:
            originals = room.alice_helper_alice()
            inp = AgentInput(
                msg=originals[0],
                messages=originals,
                tools=AgentTools(ROOM_ID, rest, participants=peer.snapshot),
                history=HistoryProvider(raw=[]),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id=ROOM_ID,
            )
            await room.adapter.on_messages(inp)
            await room.adapter.on_interrupt(ROOM_ID, mode)
            originals = room.alice_helper_alice()
            tools = AgentTools(ROOM_ID, rest, participants=peer.snapshot)
            await room.adapter.on_messages(
                replace(
                    inp,
                    msg=originals[0],
                    messages=originals,
                    tools=tools,
                    is_session_bootstrap=False,
                )
            )

        chat = [post["message"]["content"] for post in peer.posts]
        assert STALE_REPLY not in chat
        assert FRESH_REPLY in chat
        assert (tools.turn.replied, await room.pending_approvals()) == (
            True,
            "No pending approvals.",
        )
