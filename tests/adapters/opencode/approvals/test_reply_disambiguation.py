"""A reply naming a request id reaches the ask that owns that id, never a sibling."""

from __future__ import annotations

from band.testing import FakeAgentTools
from tests.adapters.opencode.approvals.kit import (
    make_room_approvals,
)
from tests.adapters.opencode.helpers import (
    AskFactory,
    FakeOpencodeClient,
    declined_questions,
)


class TestReplyDisambiguation:
    async def test_reject_by_id_targets_a_pending_question_not_a_stale_permission(
        self,
        asks: AskFactory,
    ) -> None:
        """`reject <id>` is ambiguous between the permission and question reply
        grammars. When only a question is pending under that id -- no permission
        has ever used it -- the reply must resolve as a question rejection, not
        be claimed by the permission branch and swallowed as 'no longer
        pending'."""
        client = FakeOpencodeClient()
        approvals = make_room_approvals(client)
        await approvals.on_question_asked(asks.question("q-1"))

        assert await approvals.try_handle_reply("reject q-1", "user-1")
        assert declined_questions(client) == ["q-1"]

    async def test_reject_question_id_is_not_stolen_by_a_pending_permission(
        self,
        asks: AskFactory,
    ) -> None:
        """`reject <question-id>` must reach the question while a different
        permission is also pending -- the permission branch must not consume
        it as a stale approval id."""
        client = FakeOpencodeClient()
        tools = FakeAgentTools()
        approvals = make_room_approvals(client, tools=tools)
        await approvals.on_permission_asked(asks.permission("req-1"))
        await approvals.on_question_asked(asks.question("q-1"))

        assert await approvals.try_handle_reply("reject q-1", "user-1")
        assert declined_questions(client) == ["q-1"]
        assert client.permission_replies == []
        assert approvals.awaiting_human()

    async def test_approve_question_id_does_not_answer_the_question(
        self,
        asks: AskFactory,
    ) -> None:
        """`approve <question-id>` is not the shared reject grammar -- it must
        not fall through and be submitted as a free-text question answer."""
        client = FakeOpencodeClient()
        tools = FakeAgentTools()
        approvals = make_room_approvals(client, tools=tools)
        await approvals.on_permission_asked(asks.permission("req-1"))
        await approvals.on_question_asked(asks.question("q-1"))

        assert await approvals.try_handle_reply("approve q-1", "user-1")
        assert client.question_replies == []
        assert client.permission_replies == []
        assert "no longer pending" in tools.messages_sent[-1]["content"]
        assert list(approvals._asks.permissions) == ["req-1"]
        assert list(approvals._asks.questions) == ["q-1"]

    async def test_approve_question_id_with_only_a_question_pending(
        self,
        asks: AskFactory,
    ) -> None:
        """Permission vocabulary must not be submitted as a question answer."""
        client = FakeOpencodeClient()
        tools = FakeAgentTools()
        approvals = make_room_approvals(client, tools=tools)
        await approvals.on_question_asked(asks.question("q-1"))

        assert await approvals.try_handle_reply("approve q-1", "user-1")
        assert client.question_replies == []
        assert "not `approve`/`always`" in tools.messages_sent[-1]["content"]

    async def test_bare_approve_with_only_a_question_pending(
        self, asks: AskFactory
    ) -> None:
        client = FakeOpencodeClient()
        tools = FakeAgentTools()
        approvals = make_room_approvals(client, tools=tools)
        await approvals.on_question_asked(asks.question("q-1"))

        assert await approvals.try_handle_reply("approve", "user-1")
        assert client.question_replies == []
        assert "not `approve`/`always`" in tools.messages_sent[-1]["content"]

    async def test_question_answer_starting_with_approve_is_not_a_command(
        self,
        asks: AskFactory,
    ) -> None:
        client = FakeOpencodeClient()
        approvals = make_room_approvals(client)
        await approvals.on_question_asked(asks.question("q-1"))

        assert await approvals.try_handle_reply("approve with spaces", "user-1")
        assert client.question_replies == [
            {"request_id": "q-1", "answers": [["approve with spaces"]]}
        ]

    async def test_bare_reject_with_both_permission_and_question_pending(
        self,
        asks: AskFactory,
    ) -> None:
        client = FakeOpencodeClient()
        tools = FakeAgentTools()
        approvals = make_room_approvals(client, tools=tools)
        await approvals.on_permission_asked(asks.permission("req-1"))
        await approvals.on_question_asked(asks.question("q-1"))

        assert await approvals.try_handle_reply("reject", "user-1")
        assert client.permission_replies == []
        assert declined_questions(client) == []
        assert (
            "Both an approval and a question are pending"
            in tools.messages_sent[-1]["content"]
        )

    async def test_reject_shared_id_rejects_the_question(
        self, asks: AskFactory
    ) -> None:
        client = FakeOpencodeClient()
        approvals = make_room_approvals(client)
        shared = "shared-id"
        await approvals.on_permission_asked(asks.permission(shared))
        await approvals.on_question_asked(asks.question(shared))

        assert await approvals.try_handle_reply(f"reject {shared}", "user-1")
        assert declined_questions(client) == [shared]
        assert client.permission_replies == []

    async def test_named_reject_of_a_never_asked_question_is_not_consumed(self) -> None:
        client = FakeOpencodeClient()
        approvals = make_room_approvals(client)

        assert not await approvals.try_handle_reply("reject never-asked-q", "user-1")
        assert declined_questions(client) == []

    async def test_named_reject_of_an_already_resolved_question_is_consumed(
        self,
        asks: AskFactory,
    ) -> None:
        client = FakeOpencodeClient()
        tools = FakeAgentTools()
        approvals = make_room_approvals(client, tools=tools)
        await approvals.on_question_asked(asks.question("q-1"))
        assert await approvals.try_handle_reply("reject q-1", "user-1")
        client.question_replies.clear()

        assert await approvals.try_handle_reply("reject q-1", "user-1")
        assert declined_questions(client) == []
        assert "no longer pending" in tools.messages_sent[-1]["content"]
