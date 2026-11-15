"""Approval notices address the senders of the turn that asked, for as long as it runs."""

from __future__ import annotations

import pytest

from band.core.exceptions import InputNotAccepted
from tests.adapters.claude_sdk.batch.kit import (
    ADMIN,
    ALICE,
    CAROL,
    HELPER,
    LIST_FILES,
    OpenRoom,
    alice_helper_alice,
)
from tests.adapters.claude_sdk.helpers import WRITE_NOTE, with_approvals

BATCH_AUDIENCE = [[ALICE["id"], HELPER["id"]]]


class TestApprovalAudience:
    async def test_the_request_addresses_each_distinct_sender_once(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(with_approvals())
        room.claude.script([LIST_FILES, room.model_reply("Listed.")])

        await room.send_batch(*alice_helper_alice(room))

        assert room.mentions("Approval requested") == BATCH_AUDIENCE

    async def test_a_single_original_addresses_its_one_sender(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(with_approvals())
        room.claude.script([LIST_FILES, room.model_reply("Listed.")])

        await room.send("List the files", sender=ALICE)

        assert room.mentions("Approval requested") == [[ALICE["id"]]]

    async def test_neither_a_reply_nor_a_refused_message_replaces_the_audience(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(with_approvals())
        room.claude.script([LIST_FILES, WRITE_NOTE, room.model_reply("Done.")])

        await room.send_batch(*alice_helper_alice(room))
        with pytest.raises(InputNotAccepted):
            await room.send("one more thing", sender=CAROL)
        await room.send("/approve a-1", sender=ADMIN)
        await room.until_said("Write: notes.md")
        await room.send("/approve a-2", sender=ADMIN)
        await room.settled()

        assert room.mentions("Approval requested") == BATCH_AUDIENCE * 2

    async def test_the_next_turn_addresses_its_own_senders(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(with_approvals())
        room.claude.script(
            [LIST_FILES, room.model_reply("Listed.")],
            [LIST_FILES, room.model_reply("Listed again.")],
        )

        await room.send_batch(*alice_helper_alice(room))
        await room.send("/approve", sender=ADMIN)
        await room.settled()
        await room.send("List them again", sender=CAROL)

        assert room.mentions("Approval requested") == [*BATCH_AUDIENCE, [CAROL["id"]]]

    @pytest.mark.parametrize("mode", ["auto_accept", "auto_decline"])
    async def test_a_policy_decision_addresses_the_batch(
        self, claude_room: OpenRoom, mode: str
    ) -> None:
        room = await claude_room(with_approvals(mode))
        room.claude.script([WRITE_NOTE, room.model_reply("Handled.")])

        await room.send_batch(*alice_helper_alice(room))

        assert room.mentions("Policy decision") == BATCH_AUDIENCE

    @pytest.mark.looptime
    async def test_a_timeout_notice_addresses_the_batch(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(with_approvals(wait_timeout_s=60))
        room.claude.script([LIST_FILES, room.model_reply("Done waiting.")])

        await room.send_batch(*alice_helper_alice(room))
        await room.settled()

        assert room.mentions("timed out") == BATCH_AUDIENCE
