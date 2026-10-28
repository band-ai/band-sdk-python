"""A room message answers a pending OpenCode question as free text."""

from __future__ import annotations

import asyncio

from band.integrations.opencode.types import (
    OpencodeSessionState,
)
from band.testing import FakeAgentTools
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.approvals.kit import (
    make_room_approvals,
)
from tests.adapters.opencode.helpers import (
    AskFactory,
    FakeOpencodeClient,
    event_message_updated,
    event_question,
    event_session_idle,
    event_text_part,
    make_platform_message,
    tools_protocol,
)


class TestQuestionReplies:
    async def test_question_answer_beginning_with_a_mention_is_preserved(
        self,
        asks: AskFactory,
    ) -> None:
        """A free-text answer that legitimately starts with an @handle (naming a
        person) survives: only the leading delivery mention is stripped, not the
        answer's own content."""
        client = FakeOpencodeClient()
        approvals = make_room_approvals(client)

        # Questions arrive as wire dicts (the model's before-validator keeps dicts,
        # not OpencodeQuestion instances) -- so a single question actually populates
        # and parse_question_answers takes its one-answer branch.
        await approvals.on_question_asked(asks.question("q-1"))

        assert await approvals.try_handle_reply(
            "@alexander.zaikman/tom @alice", "user-1"
        )
        assert client.question_replies == [
            {"request_id": "q-1", "answers": [["@alice"]]}
        ]

    async def test_mention_only_question_reply_requests_a_real_answer(
        self,
        asks: AskFactory,
    ) -> None:
        client = FakeOpencodeClient()
        tools = FakeAgentTools(participants=ROOM_MEMBERS)
        approvals = make_room_approvals(client, tools=tools)
        await approvals.on_question_asked(asks.question("q-1"))

        assert await approvals.try_handle_reply("@alexander.zaikman/tom", "user-1")
        assert client.question_replies == []
        assert "waiting for answers" in tools.messages_sent[-1]["content"].lower()

    async def test_malformed_question_with_no_questions_is_declined(
        self,
        asks: AskFactory,
    ) -> None:
        """A malformed question is answered with an empty decline, which hands the
        turn back to the model instead of blocking it."""
        client = FakeOpencodeClient()
        tools = FakeAgentTools(participants=ROOM_MEMBERS)
        approvals = make_room_approvals(client, tools=tools)

        await approvals.on_question_asked(asks.question("q-empty", questions=[]))

        assert client.question_replies == [{"request_id": "q-empty", "answers": []}]
        assert not approvals.awaiting_human()
        assert tools.messages_sent == []

    async def test_manual_question_reply_from_follow_up_message(
        self, make_adapter, tools
    ) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_question("sess-1", "q-1", "What should I do next?")]
            ],
            reply_question_events={
                "q-1": [
                    event_message_updated("sess-1", "msg-4"),
                    event_text_part("sess-1", "msg-4", "Question answered"),
                    event_session_idle("sess-1"),
                ]
            },
        )
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        first_turn = asyncio.create_task(
            deliver_message(
                adapter,
                make_platform_message(content="Need an answer"),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )

        await tools.until(
            lambda: any(
                "asked question" in message["content"].lower()
                for message in tools.messages_sent
            )
        )
        await asyncio.wait([first_turn])
        # Regression: FakeAgentTools accepts a call made with no mentions instead
        # of rejecting it like the real AgentTools.send_message does, so this must
        # be asserted explicitly -- it silently passed before mentions was wired.
        asked_question = next(
            m for m in tools.messages_sent if "asked question" in m["content"].lower()
        )
        assert asked_question["mentions"]

        await deliver_message(
            adapter,
            make_platform_message(content="Ship the adapter"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        await tools.until(
            lambda: any(
                message["content"] == "Question answered"
                for message in tools.messages_sent
            )
        )
        assert fake_client.question_replies == [
            {"request_id": "q-1", "answers": [["Ship the adapter"]]}
        ]
        answered = next(
            m
            for m in tools.messages_sent
            if "opencode question" in m["content"].lower()
            and "answered" in m["content"].lower()
        )
        assert answered["mentions"]
