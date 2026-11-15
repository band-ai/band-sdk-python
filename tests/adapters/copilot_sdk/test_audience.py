from __future__ import annotations

from typing import Any

from band.integrations.copilot_sdk import ASK_USER_ROOM
from band.integrations.copilot_sdk.room_ask_user import no_recipients_answer
from band.testing import FakeAgentTools, failure_reports
from tests.adapters.audiences import AGENT
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    make_started_adapter,
    requires_copilot_sdk,
    run_event,
)

pytestmark = requires_copilot_sdk


class TestCopilotAudienceQuestion:
    async def test_question_without_recipients_returns_a_truthful_acknowledgment(
        self,
    ) -> None:
        answers: list[dict[str, Any]] = []

        async def ask(session: Any) -> None:
            answers.append(
                await session.kwargs["on_user_input_request"](
                    {"question": "Continue?", "choices": ["yes", "no"]},
                    {"session_id": session.session_id},
                )
            )

        adapter = await make_started_adapter(
            FakeCopilotClient(reply_content=None, turn_events=[ask]),
            ask_user=ASK_USER_ROOM,
        )
        tools = FakeAgentTools(participants=[AGENT])
        await run_event(adapter, tools)
        assert answers == [no_recipients_answer()]
        assert tools.messages_sent == []
        assert tools.turn.complete
        assert not tools.turn.replied
        assert failure_reports(tools) == []
