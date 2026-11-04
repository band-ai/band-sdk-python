"""Feedback for a reply that resolves no live ask posts on the reply's own delivery."""

from __future__ import annotations

import pytest

from band.adapters.opencode.approvals.pending import PendingAsks
from band.adapters.opencode.approvals.wording import (
    APPROVAL_NO_LONGER_PENDING_TEMPLATE,
    QUESTION_ANSWERS_NEEDED,
    QUESTION_NO_LONGER_PENDING_TEMPLATE,
    which_dual_reject_hint,
    which_permission_hint,
    which_question_command_hint,
)
from band.testing import FakeAgentTools
from tests.adapters.audiences import ALICE, ROOM_MEMBERS
from tests.adapters.opencode.approvals.kit import asks_with, make_room_approvals
from tests.adapters.opencode.helpers import (
    AskFactory,
    FakeOpencodeClient,
    tools_protocol,
)

ONE_PERMISSION = asks_with(permissions=("p-1",))
TWO_PERMISSIONS = asks_with(permissions=("p-1", "p-2"))
ONE_QUESTION = asks_with(questions=("q-1",))
BOTH = asks_with(permissions=("p-1",), questions=("q-1",))


class TestReplyFeedback:
    @pytest.mark.parametrize(
        ("asked", "earlier_replies", "reply", "notice"),
        [
            pytest.param(
                ONE_PERMISSION,
                (),
                "approve p-stale",
                APPROVAL_NO_LONGER_PENDING_TEMPLATE.format(request_id="p-stale"),
                id="no-longer-pending",
            ),
            pytest.param(
                BOTH,
                (),
                "reject",
                which_dual_reject_hint(BOTH.permissions, BOTH.questions),
                id="dual-reject-hint",
            ),
            pytest.param(
                TWO_PERMISSIONS,
                (),
                "approve",
                which_permission_hint(TWO_PERMISSIONS.permissions),
                id="which-approval-hint",
            ),
            pytest.param(
                ONE_QUESTION,
                (),
                "approve",
                which_question_command_hint(ONE_QUESTION.questions),
                id="bare-question-command-hint",
            ),
            pytest.param(
                ONE_QUESTION,
                (),
                "approve q-1",
                which_question_command_hint(ONE_QUESTION.questions),
                id="named-question-command-hint",
            ),
            pytest.param(
                ONE_QUESTION,
                (),
                f"@{ALICE['handle']}",
                QUESTION_ANSWERS_NEEDED,
                id="answers-needed",
            ),
            pytest.param(
                ONE_QUESTION,
                ("reject q-1",),
                "reject q-1",
                QUESTION_NO_LONGER_PENDING_TEMPLATE.format(request_id="q-1"),
                id="question-gone",
            ),
            pytest.param(
                ONE_QUESTION,
                ("reject q-1",),
                "approve q-1",
                QUESTION_NO_LONGER_PENDING_TEMPLATE.format(request_id="q-1"),
                id="late-approve-of-a-question",
            ),
            pytest.param(
                ONE_QUESTION,
                ("reject q-1",),
                "always q-1",
                QUESTION_NO_LONGER_PENDING_TEMPLATE.format(request_id="q-1"),
                id="late-always-of-a-question",
            ),
            pytest.param(
                BOTH,
                ("reject q-1",),
                "reject q-1",
                QUESTION_NO_LONGER_PENDING_TEMPLATE.format(request_id="q-1"),
                id="late-reject-of-a-question-with-a-permission-pending",
            ),
            pytest.param(
                BOTH,
                ("reject q-1",),
                "approve q-1",
                QUESTION_NO_LONGER_PENDING_TEMPLATE.format(request_id="q-1"),
                id="late-approve-of-a-question-with-a-permission-pending",
            ),
            pytest.param(
                BOTH,
                (),
                "approve q-1",
                which_question_command_hint(BOTH.questions),
                id="question-command-hint-with-a-permission-pending",
            ),
            pytest.param(
                ONE_PERMISSION,
                ("approve p-1",),
                "approve p-1",
                APPROVAL_NO_LONGER_PENDING_TEMPLATE.format(request_id="p-1"),
                id="late-approve-of-a-permission",
            ),
        ],
    )
    async def test_feedback_lands_on_the_replys_tools_not_the_turns(
        self,
        asks: AskFactory,
        asked: PendingAsks,
        earlier_replies: tuple[str, ...],
        reply: str,
        notice: str,
    ) -> None:
        """The asking turn may have ended, so its tools can no longer post."""
        turn_tools = FakeAgentTools(participants=ROOM_MEMBERS)
        reply_tools = FakeAgentTools(participants=ROOM_MEMBERS)
        approvals = make_room_approvals(FakeOpencodeClient(), tools=turn_tools)
        for request_id in asked.permissions:
            await approvals.on_permission_asked(asks.permission(request_id))
        for request_id in asked.questions:
            await approvals.on_question_asked(asks.question(request_id))
        for earlier in earlier_replies:
            assert await approvals.try_handle_reply(earlier, ALICE["id"])
        turn_posts = list(turn_tools.messages_sent)

        assert await approvals.try_handle_reply(
            reply, ALICE["id"], tools_protocol(reply_tools)
        )

        assert [(m["content"], m["mentions"]) for m in reply_tools.messages_sent] == [
            (notice, [{"id": ALICE["id"]}])
        ]
        assert turn_tools.messages_sent == turn_posts
