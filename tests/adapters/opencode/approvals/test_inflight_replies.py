"""Room traffic and teardown interleave safely with a reply still in flight to OpenCode."""

from __future__ import annotations

from band.testing import FakeAgentTools
from tests.adapters.opencode.approvals.kit import (
    BlockingReplyClient,
    make_room_approvals,
)
from tests.adapters.opencode.helpers import (
    AskFactory,
    answered_questions,
    declined_questions,
)


class TestInflightReplies:
    async def test_room_traffic_while_a_permission_reply_is_in_flight(
        self,
        asks: AskFactory,
    ) -> None:
        """While the room's approval is still being sent: OpenCode's redelivery of
        that ask is a no-op, a bare `reject` skips it and rejects the open
        question, and a new permission asked meanwhile is answerable after."""
        client = BlockingReplyClient("permission")
        tools = FakeAgentTools()
        approvals = make_room_approvals(client, tools=tools)
        await approvals.on_permission_asked(asks.permission("req-1"))
        await approvals.on_question_asked(asks.question("q-1"))

        async with client.held(
            "permission", approvals.try_handle_reply("approve req-1", "user-1")
        ) as approval:
            notices_before = len(tools.messages_sent)
            await approvals.on_permission_asked(asks.permission("req-1"))
            assert len(tools.messages_sent) == notices_before

            assert await approvals.try_handle_reply("reject", "user-1")
            await approvals.on_permission_asked(
                asks.permission("req-2", permission="edit")
            )

        assert approval is not None and approval.result()
        assert await approvals.try_handle_reply("reject req-2", "user-1")
        assert [
            (reply["permission_id"], reply["reply"])
            for reply in client.permission_replies
        ] == [("req-1", "once"), ("req-2", "reject")]
        assert declined_questions(client) == ["q-1"]
        assert not approvals.awaiting_human()

    async def test_room_traffic_while_question_answers_are_in_flight(
        self,
        asks: AskFactory,
    ) -> None:
        """Free text always answers the oldest question still open -- never one
        whose answer or rejection is already being sent -- and redeliveries or
        new questions arriving meanwhile don't disturb either side."""
        client = BlockingReplyClient("question", "reject")
        tools = FakeAgentTools()
        approvals = make_room_approvals(client, tools=tools)
        for request_id in ("q-1", "q-2"):
            await approvals.on_question_asked(asks.question(request_id))

        async with client.held(
            "question", approvals.try_handle_reply("Alice", "user-1")
        ) as first:
            notices_before = len(tools.messages_sent)
            await approvals.on_question_asked(asks.question("q-1"))
            assert len(tools.messages_sent) == notices_before

            assert await approvals.try_handle_reply("Bob", "user-1")
            await approvals.on_question_asked(asks.question("q-3"))

        async with client.held(
            "reject", approvals.try_handle_reply("reject", "user-1")
        ):
            await approvals.on_question_asked(asks.question("q-4"))
        assert await approvals.try_handle_reply("Now", "user-1")

        assert first is not None and first.result()
        assert answered_questions(client) == [
            ("q-2", [["Bob"]]),
            ("q-1", [["Alice"]]),
            ("q-4", [["Now"]]),
        ]
        assert declined_questions(client) == ["q-3"]
        assert not approvals.awaiting_human()

    async def test_a_late_reply_never_forgets_the_same_id_asked_again(
        self,
        asks: AskFactory,
    ) -> None:
        """Teardown drops an ask whose reply is in flight and OpenCode asks the
        same id again; the old reply finishing must leave the new ask pending."""
        client = BlockingReplyClient("permission")
        approvals = make_room_approvals(client)
        request = asks.permission("req-1")
        await approvals.on_permission_asked(request)

        async with client.held(
            "permission", approvals.try_handle_reply("approve req-1", "user-1")
        ):
            approvals.cancel()
            await approvals.on_permission_asked(request)

        assert approvals.awaiting_human()
        assert await approvals.try_handle_reply("approve req-1", "user-1")
        assert [reply["permission_id"] for reply in client.permission_replies] == [
            "req-1",
            "req-1",
        ]
