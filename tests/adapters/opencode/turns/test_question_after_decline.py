"""OpenCode: a question the model asks after declining is rejected, not posted."""

from __future__ import annotations

from band.adapters.opencode.approvals.wording import DECLINED_QUESTION_ANSWER
from band.testing import FakeAgentTools
from tests.adapters.opencode.helpers import (
    BandMCPCalls,
    FakeOpencodeClient,
    event_message_updated,
    event_question,
    event_session_idle,
    run_single_turn,
)
from tests.framework_conformance.outcomeprobes import DECLINE


class TestQuestionAfterDecline:
    async def test_the_question_is_rejected_and_never_reaches_the_room(
        self, make_adapter, tools: FakeAgentTools, mcp_backend: BandMCPCalls
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-1"),
                    mcp_backend.band_tool_call(DECLINE.name, DECLINE.arguments),
                    event_question("sess-1", "q-1", "Which vault?"),
                    event_session_idle("sess-1"),
                ]
            ]
        )

        await run_single_turn(make_adapter(client), tools)

        assert client.question_replies == [
            {"request_id": "q-1", "answers": [[DECLINED_QUESTION_ANSWER]]}
        ]
        assert tools.chat == []
