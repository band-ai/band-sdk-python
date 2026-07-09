"""ask_user="room": questions post to the room, the turn ends, answers come later.

The room bridge is exercised through the session's registered
``on_user_input_request`` handler — the same seam the Copilot SDK
dispatches ``userInput.request`` RPCs to — via the fakes' callable
turn-event hook that simulates mid-turn tool execution.
"""

from __future__ import annotations

from typing import Any

import pytest

from band.adapters.copilot_sdk import CopilotSDKAdapterConfig
from band.integrations.copilot_sdk import ASK_USER_ROOM, render_room_question
from band.integrations.copilot_sdk.room_ask_user import (
    delivery_failed_answer,
    question_delivered_answer,
    room_inactive_answer,
)
from band.testing import FakeAgentTools
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)

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
        adapter = await make_started_adapter(
            client, CopilotSDKAdapterConfig(ask_user=ASK_USER_ROOM)
        )
        tools = FakeAgentTools()

        await run_message(adapter, tools)

        rendered = render_room_question(QUESTION)
        assert len(tools.messages_sent) == 1
        posted = tools.messages_sent[0]
        assert posted["content"] == rendered
        # The question addresses whoever triggered the turn.
        assert posted["mentions"] == [{"id": "user-1", "name": "Alice"}]
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
        adapter = await make_started_adapter(
            client, CopilotSDKAdapterConfig(ask_user=ASK_USER_ROOM)
        )
        tools = FakeAgentTools()

        await run_message(adapter, tools)

        contents = [m["content"] for m in tools.messages_sent]
        assert contents == [render_room_question(QUESTION)]

    @pytest.mark.asyncio
    async def test_delivery_failure_degrades_to_answer_not_rpc_error(self):
        """A failed post must not blow up the turn as an opaque RPC failure."""

        class FailingOnceTools(FakeAgentTools):
            def __init__(self) -> None:
                super().__init__()
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
        adapter = await make_started_adapter(
            client, CopilotSDKAdapterConfig(ask_user=ASK_USER_ROOM)
        )
        tools = FailingOnceTools()

        await run_message(adapter, tools)

        assert answers == [delivery_failed_answer(RuntimeError("platform down"))]
        # The turn itself survives: the model's reply reaches the room.
        assert [m["content"] for m in tools.messages_sent] == [
            "Proceeding without input."
        ]

    @pytest.mark.asyncio
    async def test_late_dispatch_after_turn_end_degrades_gracefully(self):
        """The SDK never cancels a pending ask; a post-turn dispatch must
        answer benignly instead of crashing on missing turn state."""
        client = FakeCopilotClient()
        adapter = await make_started_adapter(
            client, CopilotSDKAdapterConfig(ask_user=ASK_USER_ROOM)
        )
        tools = FakeAgentTools()
        await run_message(adapter, tools)

        handler = client.sessions[0].kwargs["on_user_input_request"]
        answer = await handler(QUESTION, {"session_id": "s"})

        assert answer == room_inactive_answer()
        assert len(tools.messages_sent) == 1  # only the turn's own reply
