"""Recognizing a room message as a reply to an OpenCode ask reads pending asks
and writes nothing: the runtime classifies before any processing write, so a
probe that claimed an ask, stopped its timer or posted would act on mail that
may turn out to be ordinary work.
"""

from __future__ import annotations

import asyncio

import pytest

from band.adapters.opencode import OpencodeAdapterConfig
from band.adapters.opencode.approvals import REJECTED_PERMISSION_FEEDBACK
from band.testing import FakeAgentTools
from tests.adapters.opencode.conftest import ParkRoom
from tests.adapters.opencode.helpers import (
    ASK_DEADLINE_S,
    PERMISSION_ASK,
    QUESTION_ASK,
    TWO_QUESTIONS_ASK,
    HeldPermissionReplyClient,
    RawOpencodeEvent,
    answered_questions,
    client_asking,
    declined_questions,
    handle_control,
    room_reply,
    time_passes,
    tools_protocol,
)


class TestControlRecognition:
    async def test_free_text_answers_a_pending_question(
        self, park_room: ParkRoom
    ) -> None:
        adapter = await park_room(client_asking(QUESTION_ASK))

        assert [
            adapter.is_control_message(room_reply(text))
            for text in ("blue", "@agent blue")
        ] == [True, True]

    @pytest.mark.parametrize(
        ("content", "is_control"),
        [
            ("blue", False),
            ("please approve", False),
            ("approve req-1", True),
            ("always req-1", True),
            ("reject req-1", True),
            ("@agent /approve", True),
        ],
    )
    async def test_only_the_approval_vocabulary_replies_to_a_pending_approval(
        self, park_room: ParkRoom, content: str, is_control: bool
    ) -> None:
        adapter = await park_room(client_asking(PERMISSION_ASK))

        assert adapter.is_control_message(room_reply(content)) is is_control

    async def test_the_answer_consumed_the_question_so_the_next_text_is_ordinary(
        self, park_room: ParkRoom
    ) -> None:
        client = client_asking(QUESTION_ASK)
        adapter = await park_room(client)
        fyi = room_reply("the build is green again")

        verdicts = [adapter.is_control_message(fyi)]
        assert await handle_control(adapter, room_reply("blue"))
        verdicts.append(adapter.is_control_message(fyi))

        assert verdicts == [True, False]
        assert answered_questions(client) == [("q-1", [["blue"]])]

    @pytest.mark.parametrize(
        ("ask", "content"),
        [
            pytest.param(PERMISSION_ASK, "approve req-stale", id="unknown-request"),
            pytest.param(QUESTION_ASK, "reject q-stale", id="unknown-question"),
            pytest.param(
                TWO_QUESTIONS_ASK, "one line for two questions", id="too-few-lines"
            ),
        ],
    )
    async def test_a_reply_the_handler_cannot_apply_is_still_a_control(
        self, park_room: ParkRoom, ask: RawOpencodeEvent, content: str
    ) -> None:
        client = client_asking(ask)
        adapter = await park_room(client)
        message = room_reply(content)

        assert adapter.is_control_message(message)
        assert await handle_control(adapter, message)
        assert (client.permission_replies, client.question_replies) == ([], [])

    async def test_recognizing_replies_posts_and_claims_nothing(
        self, park_room: ParkRoom, tools: FakeAgentTools
    ) -> None:
        client = client_asking(PERMISSION_ASK)
        adapter = await park_room(client)
        posted_before = list(tools.messages_sent)

        for content in ("approve req-1", "approve", "reject req-1", "plain text"):
            adapter.is_control_message(room_reply(content))

        assert tools.messages_sent == posted_before
        assert client.permission_replies == []
        assert await handle_control(adapter, room_reply("approve req-1"))
        assert client.permission_replies == [
            {"permission_id": "req-1", "reply": "once"}
        ]

    @pytest.mark.looptime
    async def test_recognizing_a_reply_does_not_stop_its_ask_expiring(
        self, park_room: ParkRoom
    ) -> None:
        client = client_asking(PERMISSION_ASK)
        adapter = await park_room(
            client, config=OpencodeAdapterConfig(approval_wait_timeout_s=ASK_DEADLINE_S)
        )

        assert adapter.is_control_message(room_reply("approve req-1"))
        await time_passes(ASK_DEADLINE_S + 1)

        assert client.permission_replies == [
            {
                "permission_id": "req-1",
                "reply": "reject",
                "message": REJECTED_PERMISSION_FEEDBACK,
            }
        ]

    @pytest.mark.looptime
    async def test_an_ask_that_expired_after_recognition_is_not_consumed(
        self, park_room: ParkRoom
    ) -> None:
        client = client_asking(QUESTION_ASK)
        adapter = await park_room(
            client, config=OpencodeAdapterConfig(question_wait_timeout_s=ASK_DEADLINE_S)
        )
        answer = room_reply("blue")

        assert adapter.is_control_message(answer)
        await time_passes(ASK_DEADLINE_S + 1)

        assert not await handle_control(adapter, answer)
        assert not adapter.is_control_message(answer)
        assert answered_questions(client) == []
        assert declined_questions(client) == ["q-1"]

    async def test_a_claimed_request_is_resolved_once_whoever_else_replies(
        self, park_room: ParkRoom
    ) -> None:
        client = HeldPermissionReplyClient(prompt_event_sequences=[[PERMISSION_ASK]])
        adapter = await park_room(client)
        first, second = (
            room_reply("approve req-1", sender_id="user-2"),
            room_reply("approve req-1", sender_id="user-3"),
        )
        second_tools = FakeAgentTools()

        resolving = asyncio.create_task(handle_control(adapter, first))
        await client.reply_in_flight.wait()
        second_recognized = adapter.is_control_message(second)
        second_consumed = await adapter.try_handle_control(
            second, tools_protocol(second_tools)
        )
        client.release_reply.set()
        await resolving

        assert (second_recognized, second_consumed) == (True, True)
        assert client.permission_replies == [
            {"permission_id": "req-1", "reply": "once"}
        ]
        assert second_tools.messages_sent == []
