"""Automatic notices address the senders of the turn that raised them, for as long as it runs."""

from __future__ import annotations

import pytest

from band.core.exceptions import InputNotAccepted
from tests.adapters.codexbatch.kit import (
    ADMIN,
    ALICE,
    CAROL,
    HELPER,
    OpenRoom,
    approval_request,
    reply_to_room,
)
from tests.adapters.codexturns import turn_completed

BATCH_AUDIENCE = [[ALICE["id"], HELPER["id"]]]


class TestApprovalAudience:
    async def test_the_request_addresses_each_distinct_sender_once(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(approval_request(10), turn_completed())

        await room.send_batch(*room.alice_helper_alice())

        assert room.mentions("Approval requested") == BATCH_AUDIENCE

    async def test_a_single_original_addresses_its_one_sender(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(approval_request(10), turn_completed())

        await room.send("run tests", sender=ALICE)

        assert room.mentions("Approval requested") == [[ALICE["id"]]]

    async def test_neither_a_reply_nor_a_refused_message_replaces_the_audience(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(
            approval_request(10),
            approval_request(11),
            reply_to_room(),
            turn_completed(),
        )

        await room.send_batch(*room.alice_helper_alice())
        with pytest.raises(InputNotAccepted):
            await room.send("one more thing", sender=CAROL)
        await room.control(f"/approve {await room.approval_token(1)}", sender=ADMIN)
        await room.control(f"/approve {await room.approval_token(2)}", sender=ADMIN)
        await room.settled()

        assert room.mentions("Approval requested") == BATCH_AUDIENCE * 2

    async def test_the_next_turn_addresses_its_own_senders(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(
            approval_request(10),
            reply_to_room(),
            turn_completed(),
            approval_request(11),
            turn_completed("turn-2"),
        )

        await room.send_batch(*room.alice_helper_alice())
        await room.control(f"/approve {await room.approval_token()}", sender=ADMIN)
        await room.settled()
        await room.send("run tests again", sender=CAROL)

        assert room.mentions("Approval requested") == [*BATCH_AUDIENCE, [CAROL["id"]]]

    @pytest.mark.parametrize("mode", ["auto_accept", "auto_decline"])
    async def test_a_policy_decision_addresses_the_batch(
        self, codex_room: OpenRoom, mode: str
    ) -> None:
        room = await codex_room(
            approval_request(10),
            reply_to_room(),
            turn_completed(),
            approval_mode=mode,
            approval_text_notifications=True,
        )

        await room.send_batch(*room.alice_helper_alice())

        assert room.mentions("Policy decision") == BATCH_AUDIENCE

    @pytest.mark.looptime
    async def test_a_timeout_notice_addresses_the_batch(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(
            approval_request(10),
            turn_completed(),
            approval_wait_timeout_s=60,
        )

        await room.send_batch(*room.alice_helper_alice())
        await room.settled()

        assert room.mentions("timed out") == BATCH_AUDIENCE

    async def test_an_interrupted_turn_tells_the_whole_batch(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(turn_completed(status="interrupted"))

        await room.send_batch(*room.alice_helper_alice())

        assert room.mentions("I stopped before completing") == BATCH_AUDIENCE
