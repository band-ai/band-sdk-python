"""Approvals stay individual: each has its own request and its own replier."""

from __future__ import annotations

from tests.adapters.codexbatch.kit import (
    ADMIN,
    ALICE,
    HELPER,
    OpenRoom,
    approval_request,
    reply_to_room,
)
from tests.adapters.codexturns import turn_completed


class TestIndividualApprovals:
    async def test_each_reply_resolves_only_its_own_request(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(
            approval_request(10),
            approval_request(11),
            reply_to_room(),
            turn_completed(),
        )

        await room.send_batch(*room.alice_helper_alice())
        await room.control(f"/decline {await room.approval_token(1)}", sender=HELPER)
        await room.control(f"/approve {await room.approval_token(2)}", sender=ALICE)
        await room.settled()

        assert room.decisions == [(10, "decline"), (11, "accept")]

    async def test_each_reply_is_answered_to_its_own_sender(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(
            approval_request(10),
            approval_request(11),
            reply_to_room(),
            turn_completed(),
        )

        await room.send_batch(*room.alice_helper_alice())
        declined = await room.control(
            f"/decline {await room.approval_token(1)}", sender=HELPER
        )
        approved = await room.control(
            f"/approve {await room.approval_token(2)}", sender=ADMIN
        )
        await room.settled()

        assert [
            [mention["id"] for mention in message["mentions"]]
            for answer in (declined, approved)
            for message in answer.messages_sent
        ] == [[HELPER["id"]], [ADMIN["id"]]]
