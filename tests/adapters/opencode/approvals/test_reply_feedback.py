"""Feedback for a reply that resolves no live ask posts on the reply's own delivery."""

from __future__ import annotations

import pytest

from band.testing import FakeAgentTools
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.opencode.approvals.kit import make_room_approvals
from tests.adapters.opencode.helpers import (
    AskFactory,
    FakeOpencodeClient,
    tools_protocol,
)


class TestReplyFeedback:
    @pytest.mark.parametrize(
        ("permissions", "questions", "earlier_replies", "reply"),
        [
            pytest.param(("p-1",), (), (), "approve p-stale", id="no-longer-pending"),
            pytest.param(("p-1",), ("q-1",), (), "reject", id="dual-reject-hint"),
            pytest.param(("p-1", "p-2"), (), (), "approve", id="which-approval-hint"),
            pytest.param((), ("q-1",), (), "approve", id="bare-question-command-hint"),
            pytest.param(
                (), ("q-1",), (), "approve q-1", id="named-question-command-hint"
            ),
            pytest.param(
                (), ("q-1",), (), "@alexander.zaikman/tom", id="answers-needed"
            ),
            pytest.param(
                (), ("q-1",), ("reject q-1",), "reject q-1", id="question-gone"
            ),
        ],
    )
    async def test_feedback_lands_on_the_replys_tools_not_the_turns(
        self,
        asks: AskFactory,
        permissions: tuple[str, ...],
        questions: tuple[str, ...],
        earlier_replies: tuple[str, ...],
        reply: str,
    ) -> None:
        """The asking turn may have ended, so its tools can no longer post."""
        turn_tools = FakeAgentTools(participants=ROOM_MEMBERS)
        reply_tools = FakeAgentTools(participants=ROOM_MEMBERS)
        approvals = make_room_approvals(FakeOpencodeClient(), tools=turn_tools)
        for request_id in permissions:
            await approvals.on_permission_asked(asks.permission(request_id))
        for request_id in questions:
            await approvals.on_question_asked(asks.question(request_id))
        for earlier in earlier_replies:
            assert await approvals.try_handle_reply(earlier, "user-1")
        turn_posts = list(turn_tools.messages_sent)

        assert await approvals.try_handle_reply(
            reply, "user-1", tools_protocol(reply_tools)
        )

        assert len(reply_tools.messages_sent) == 1
        assert turn_tools.messages_sent == turn_posts
