"""ask_user="room": questions post to the room, the turn ends, answers come later.

The room bridge is exercised through the session's registered
``on_user_input_request`` handler — the same seam the Copilot SDK
dispatches ``userInput.request`` RPCs to — via the fakes' callable
turn-event hook that simulates mid-turn tool execution.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.integrations.copilot_sdk import ASK_USER_ROOM, render_room_question
from band.integrations.copilot_sdk.room_ask_user import (
    delivery_failed_answer,
    question_delivered_answer,
    room_inactive_answer,
)
from band.testing import FakeAgentTools
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    ObservedCopilotAdapter,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)
from tests.framework_conformance.batchprobes import (
    ALICE_FIRST,
    BATCH,
    HELPER,
    deliver,
    delivery_input,
    posted_messages,
)
from tests.toolkit.tasks import background

pytestmark = requires_copilot_sdk

QUESTION = {
    "question": "Which channel should I deploy to?",
    "choices": ["stable", "beta", "canary"],
    "allowFreeform": True,
}


def ask_mid_turn(request: dict[str, Any], answers: list[dict[str, Any]]) -> Any:
    """Turn-event callable: dispatch ``request`` to the session's handler."""

    async def invoke(session: Any) -> None:
        handler = session.kwargs["on_user_input_request"]
        answers.append(await handler(request, {"session_id": session.session_id}))

    return invoke


class TestAskUserRoom:
    @pytest.mark.asyncio
    async def test_question_posts_to_room_and_acks_the_tool_call(self):
        answers: list[dict[str, Any]] = []
        client = FakeCopilotClient(turn_events=[ask_mid_turn(QUESTION, answers)])
        adapter = await make_started_adapter(client, ask_user=ASK_USER_ROOM)
        tools = FakeAgentTools(participants=ROOM_MEMBERS)

        await run_message(adapter, tools)

        rendered = render_room_question(QUESTION)
        assert len(tools.messages_sent) == 1
        posted = tools.messages_sent[0]
        assert posted["content"] == rendered
        # The question addresses whoever triggered the turn.
        assert posted["mentions"] == [{"id": "user-1"}]
        # The ack echoes the rendered form so the model can map a bare
        # numeric answer back to its numbered choices.
        assert answers == [question_delivered_answer(rendered)]

    @pytest.mark.asyncio
    async def test_question_is_the_turn_reply_final_text_suppressed(self):
        """A "waiting for your answer" wrap-up must not shadow the question."""
        answers: list[dict[str, Any]] = []
        client = FakeCopilotClient(
            reply_content="I'll wait for your answer.",
            turn_events=[ask_mid_turn(QUESTION, answers)],
        )
        adapter = await make_started_adapter(client, ask_user=ASK_USER_ROOM)
        tools = FakeAgentTools(participants=ROOM_MEMBERS)

        await run_message(adapter, tools)

        contents = tools.chat
        assert contents == [render_room_question(QUESTION)]

    @pytest.mark.asyncio
    async def test_delivery_failure_degrades_to_answer_not_rpc_error(self):
        """A failed post must not blow up the turn as an opaque RPC failure."""

        class FailingOnceTools(FakeAgentTools):
            def __init__(self) -> None:
                super().__init__(participants=ROOM_MEMBERS)
                self.failed = False

            async def send_message(self, content, mentions=None):
                if not self.failed:
                    self.failed = True
                    raise RuntimeError("platform down")
                return await super().send_message(content, mentions)

        answers: list[dict[str, Any]] = []
        client = FakeCopilotClient(
            reply_content="Proceeding without input.",
            turn_events=[ask_mid_turn(QUESTION, answers)],
        )
        adapter = await make_started_adapter(client, ask_user=ASK_USER_ROOM)
        tools = FailingOnceTools()

        await run_message(adapter, tools)

        assert answers == [delivery_failed_answer(RuntimeError("platform down"))]
        # The turn itself survives: the model's reply reaches the room.
        assert tools.chat == ["Proceeding without input."]

    @pytest.mark.asyncio
    async def test_late_dispatch_after_turn_end_degrades_gracefully(self):
        """The SDK never cancels a pending ask; a post-turn dispatch must
        answer benignly instead of crashing on missing turn state."""
        client = FakeCopilotClient()
        adapter = await make_started_adapter(client, ask_user=ASK_USER_ROOM)
        tools = FakeAgentTools(participants=ROOM_MEMBERS)
        await run_message(adapter, tools)

        handler = client.sessions[0].kwargs["on_user_input_request"]
        answer = await handler(QUESTION, {"session_id": "s"})

        assert answer == room_inactive_answer()
        assert len(tools.messages_sent) == 1  # only the turn's own reply

    @pytest.mark.asyncio
    async def test_question_in_a_batch_turn_addresses_each_distinct_sender(self):
        answers: list[dict[str, Any]] = []
        client = FakeCopilotClient(turn_events=[ask_mid_turn(QUESTION, answers)])
        adapter = await make_started_adapter(client, ask_user=ASK_USER_ROOM)
        inp = await delivery_input(BATCH)

        await deliver(adapter, inp)

        assert [post["mentions"] for post in posted_messages(inp)] == [
            [{"id": "alice"}, {"id": "helper"}]
        ]

    @pytest.mark.asyncio
    async def test_a_queued_turn_does_not_redirect_the_running_turns_question(self):
        """The question goes to the senders the running turn answers, even
        when a message from someone else is already waiting for the room."""
        parked = asyncio.Event()
        released = asyncio.Event()
        answers: list[dict[str, Any]] = []

        async def ask_once_released(session: Any) -> None:
            if ALICE_FIRST.content not in session.prompts[-1]:  # the queued turn
                return
            parked.set()
            await released.wait()
            await ask_mid_turn(QUESTION, answers)(session)

        client = FakeCopilotClient(turn_events=[ask_once_released])
        adapter = ObservedCopilotAdapter(
            client_factory=lambda: client, ask_user=ASK_USER_ROOM
        )
        await adapter.on_started("Copilot Agent", "A helpful test agent")
        running = await delivery_input(BATCH)
        queued = await delivery_input([HELPER])

        async with background(deliver(adapter, running)) as running_turn:
            try:
                await parked.wait()
                assert await adapter.turn_entries.get() == tuple(
                    m.id for m in running.messages
                )
                async with background(deliver(adapter, queued)) as queued_turn:
                    try:
                        assert await adapter.turn_entries.get() == tuple(
                            m.id for m in queued.messages
                        )
                    finally:
                        released.set()
                    await asyncio.gather(running_turn, queued_turn)
            finally:
                released.set()

        assert [
            (post["content"], post["mentions"]) for post in posted_messages(running)
        ] == [(render_room_question(QUESTION), [{"id": "alice"}, {"id": "helper"}])]
        assert [post["mentions"] for post in posted_messages(queued)] == [
            [{"id": "helper"}]
        ]
