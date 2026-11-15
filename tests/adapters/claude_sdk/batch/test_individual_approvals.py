"""Approvals stay individual: own request, own replier, configured authorization."""

from __future__ import annotations

from band.adapters.claude_sdk import APPROVAL_UNAUTHORIZED_MESSAGE
from tests.adapters.claude_sdk.batch.kit import (
    ADMIN,
    ALICE,
    DECLINED,
    HELPER,
    LIST_FILES,
    OpenRoom,
    alice_helper_alice,
)
from tests.adapters.claude_sdk.helpers import WRITE_NOTE, with_approvals
from tests.baseline.decisions import ModelDecision, ToolCall

BASH_AND_WRITE = ModelDecision(
    tool_calls=(
        ToolCall("Bash", {"command": "ls"}),
        ToolCall("Write", {"file_path": "notes.md", "content": "todo"}),
    )
)


class TestIndividualApprovals:
    async def test_each_reply_resolves_only_its_own_request(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(with_approvals())
        room.claude.script([BASH_AND_WRITE, room.model_reply("Done.")])

        await room.send_batch(*alice_helper_alice(room))
        await room.until_said("Approval requested", times=2)
        await room.send("/decline a-1", sender=HELPER)
        await room.send("/approve a-2", sender=ALICE)
        await room.settled()

        assert room.tool_outputs["Bash"] == DECLINED
        assert room.tool_outputs["Write"] == "Write ran"

    async def test_each_reply_is_answered_to_its_own_sender(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(with_approvals())
        room.claude.script([BASH_AND_WRITE, room.model_reply("Done.")])

        await room.send_batch(*alice_helper_alice(room))
        await room.until_said("Approval requested", times=2)
        await room.send("/decline a-1", sender=HELPER)
        await room.send("/approve a-2", sender=ADMIN)
        await room.settled()

        assert room.mentions("resolved") == [[HELPER["id"]], [ADMIN["id"]]]

    async def test_batch_senders_are_not_authorized_by_being_in_the_batch(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(with_approvals(authorized_senders={ADMIN["id"]}))
        room.claude.script([LIST_FILES, room.model_reply("Listed.")])

        await room.send_batch(*alice_helper_alice(room))
        await room.send("/approve a-1", sender=ALICE)
        await room.send("/approve a-1", sender=HELPER)
        refused = room.mentions(APPROVAL_UNAUTHORIZED_MESSAGE)
        await room.send("/approve a-1", sender=ADMIN)
        await room.settled()

        assert refused == [[ALICE["id"]], [HELPER["id"]]]
        assert room.tool_outputs["Bash"] == "Bash ran"

    async def test_a_request_notifies_senders_who_cannot_answer_it(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(with_approvals(authorized_senders={ADMIN["id"]}))
        room.claude.script([WRITE_NOTE, room.model_reply("Noted.")])

        await room.send_batch(*alice_helper_alice(room))

        assert room.mentions("Approval requested") == [[ALICE["id"], HELPER["id"]]]
