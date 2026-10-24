"""Control discovery reads pending asks without consuming their replies."""

from __future__ import annotations

from collections.abc import Callable

from band.adapters.opencode import OpencodeAdapter
from band.testing import FakeAgentTools
from tests.adapters.dispatch import deliver_event
from tests.adapters.opencode.approvals.kit import (
    BlockingReplyClient,
    make_room_approvals,
)
from tests.adapters.opencode.helpers import (
    APPROVAL_REPLY,
    PERMISSION_ID,
    AskFactory,
    FakeOpencodeClient,
    agent_input,
    answered_questions,
    approve,
    event_question,
    make_platform_message,
    model_answer,
    park_turn,
    parked_turn_client,
    room_turn,
    tools_protocol,
    turn_finished,
)


class TestControlRecognition:
    async def test_permission_discovery_leaves_the_reply_for_its_handler(
        self,
        make_adapter: Callable[..., OpencodeAdapter],
        tools: FakeAgentTools,
    ) -> None:
        client = parked_turn_client(after_approval=model_answer("answer", "Done"))
        adapter = make_adapter(client)
        reply = make_platform_message(content=APPROVAL_REPLY)
        assert not adapter.is_control_message(reply)

        await park_turn(adapter, tools)
        notices = list(tools.messages_sent)
        assert adapter.is_control_message(reply)
        assert adapter.is_control_message(reply)
        assert not adapter.is_control_message(make_platform_message(content="hello"))
        assert tools.messages_sent == notices
        assert client.permission_replies == []

        await approve(adapter)
        await turn_finished(await room_turn(adapter))
        assert client.permission_replies == [
            {"permission_id": PERMISSION_ID, "reply": "once"}
        ]
        assert not adapter.is_control_message(make_platform_message(content="approve"))

    async def test_free_text_is_a_control_only_while_a_question_is_pending(
        self,
        make_adapter: Callable[..., OpencodeAdapter],
        tools: FakeAgentTools,
    ) -> None:
        request_id = "question"
        client = FakeOpencodeClient(
            prompt_event_sequences=[
                [event_question("sess-1", request_id, "Which color?")]
            ],
            reply_question_events={request_id: model_answer("answer", "Done")},
        )
        adapter = make_adapter(client)
        answer = "blue"
        reply = make_platform_message(content=answer)
        assert not adapter.is_control_message(reply)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_event(adapter, agent_input("Choose a color", tools))
        notices = list(tools.messages_sent)
        assert adapter.is_control_message(reply)
        assert adapter.is_control_message(reply)
        assert tools.messages_sent == notices
        assert client.question_replies == []

        assert await adapter.try_handle_control(reply, tools_protocol(tools))
        await turn_finished(await room_turn(adapter))
        assert client.question_replies == [
            {"request_id": request_id, "answers": [[answer]]}
        ]
        assert not adapter.is_control_message(reply)

    async def test_discovery_uses_the_remaining_open_questions_during_a_reply(
        self, asks: AskFactory
    ) -> None:
        client = BlockingReplyClient("question")
        approvals = make_room_approvals(client)
        answer = "blue"
        await approvals.on_question_asked(asks.question("first"))

        async with client.held(
            "question", approvals.try_handle_reply(answer, "user-1")
        ):
            assert not approvals.recognizes_reply(answer)
            await approvals.on_question_asked(asks.question("next"))
            assert approvals.recognizes_reply(answer)
            assert await approvals.try_handle_reply(answer, "user-1")

        assert answered_questions(client) == [
            ("next", [[answer]]),
            ("first", [[answer]]),
        ]
        assert not approvals.recognizes_reply(answer)
