"""A named reply to an ask whose answer is already in flight is told it lost."""

from __future__ import annotations

from typing import Any

import pytest

from band.adapters.opencode import OpencodeAdapterConfig
from band.adapters.opencode.approvals.wording import (
    APPROVAL_NO_LONGER_PENDING_TEMPLATE,
    DECLINED_QUESTION_ANSWER,
    QUESTION_NO_LONGER_PENDING_TEMPLATE,
)
from band.testing import FakeAgentTools
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.opencode.approvals.kit import (
    BlockingReplyClient,
    RaiseAsk,
    ReplyOperation,
    make_room_approvals,
)
from tests.adapters.opencode.conftest import ParkRoom
from tests.adapters.opencode.helpers import (
    ASK_DEADLINE_S,
    PERMISSION_ASK,
    QUESTION_ASK,
    AskFactory,
    FakeOpencodeClient,
    RawOpencodeEvent,
    notice_to,
    room_reply,
    tools_protocol,
)

LOSER = "user-2"


def replies_sent(client: FakeOpencodeClient) -> list[tuple[str, Any]]:
    """Every reply OpenCode received, as (request id, reply or answers)."""
    return [
        (reply["permission_id"], reply["reply"]) for reply in client.permission_replies
    ] + [(reply["request_id"], reply["answers"]) for reply in client.question_replies]


class TestConflictingReplies:
    @pytest.mark.parametrize(
        ("raise_ask", "operation", "claim", "conflicting", "notice", "sent"),
        [
            pytest.param(
                lambda room, asks: room.on_permission_asked(asks.permission("req-1")),
                "permission",
                "approve req-1",
                "reject req-1",
                APPROVAL_NO_LONGER_PENDING_TEMPLATE.format(request_id="req-1"),
                [("req-1", "once")],
                id="permission",
            ),
            pytest.param(
                lambda room, asks: room.on_question_asked(asks.question("q-1")),
                "question",
                "Alice",
                "reject q-1",
                QUESTION_NO_LONGER_PENDING_TEMPLATE.format(request_id="q-1"),
                [("q-1", [["Alice"]])],
                id="question",
            ),
        ],
    )
    async def test_a_named_reply_to_a_held_ask_gets_the_same_notice_as_after_it_lands(
        self,
        asks: AskFactory,
        raise_ask: RaiseAsk,
        operation: ReplyOperation,
        claim: str,
        conflicting: str,
        notice: str,
        sent: list[tuple[str, Any]],
    ) -> None:
        """Bob names an ask while Alice's answer to it is still being sent (two
        controls racing, as in one-shot mode): Bob's reply is consumed and he is
        told the ask is no longer pending, on his own delivery; OpenCode gets
        only Alice's answer; after it lands, Bob gets the same notice again."""
        client = BlockingReplyClient(operation)
        approvals = make_room_approvals(client)
        await raise_ask(approvals, asks)
        during, after = (FakeAgentTools(participants=ROOM_MEMBERS) for _ in range(2))

        async with client.held(operation, approvals.try_handle_reply(claim, "user-1")):
            assert await approvals.try_handle_reply(
                conflicting, LOSER, tools_protocol(during)
            )
        assert await approvals.try_handle_reply(
            conflicting, LOSER, tools_protocol(after)
        )

        assert during.messages_sent == [notice_to(LOSER, notice)]
        assert after.messages_sent == during.messages_sent
        assert replies_sent(client) == sent

    @pytest.mark.looptime
    @pytest.mark.parametrize(
        ("ask", "operation", "conflicting", "notice", "sent"),
        [
            pytest.param(
                PERMISSION_ASK,
                "permission",
                "approve req-1",
                APPROVAL_NO_LONGER_PENDING_TEMPLATE.format(request_id="req-1"),
                [("req-1", "reject")],
                id="permission",
            ),
            pytest.param(
                QUESTION_ASK,
                "reject",
                "reject q-1",
                QUESTION_NO_LONGER_PENDING_TEMPLATE.format(request_id="q-1"),
                [("q-1", [[DECLINED_QUESTION_ANSWER]])],
                id="question",
            ),
        ],
    )
    async def test_a_named_reply_during_its_expiry_is_told_so(
        self,
        park_room: ParkRoom,
        tools: FakeAgentTools,
        ask: RawOpencodeEvent,
        operation: ReplyOperation,
        conflicting: str,
        notice: str,
        sent: list[tuple[str, Any]],
    ) -> None:
        """An ask's wait expires and its timeout reply is still being sent when
        someone answers it by id: the adapter consumes that control and tells
        the sender, on the control's own delivery, that the ask is no longer
        pending; OpenCode gets only the timeout reply."""
        client = BlockingReplyClient(operation, prompt_event_sequences=[[ask]])
        adapter = await park_room(
            client,
            config=OpencodeAdapterConfig(
                approval_mode="manual",
                approval_wait_timeout_s=ASK_DEADLINE_S,
                approval_timeout_reply="reject",
                question_mode="manual",
                question_wait_timeout_s=ASK_DEADLINE_S,
            ),
        )
        reply_tools = FakeAgentTools(participants=ROOM_MEMBERS)

        async with client.held(operation):
            assert await adapter.try_handle_control(
                room_reply(conflicting, LOSER), tools_protocol(reply_tools)
            )
        await tools.until(lambda: bool(tools.events_sent))

        assert reply_tools.messages_sent == [notice_to(LOSER, notice)]
        assert replies_sent(client) == sent
