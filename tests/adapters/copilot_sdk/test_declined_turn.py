"""Once the model declines, Copilot's later tool calls and questions do nothing."""

from __future__ import annotations

from typing import Any

import pytest

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE
from band.integrations.copilot_sdk import ASK_USER_ROOM
from band.integrations.copilot_sdk.room_ask_user import freeform_answer
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from tests.adapters.copilot_sdk.declinekit import DECLINE, refused
from tests.adapters.copilot_sdk.fakes import (
    QUESTION,
    FakeCopilotClient,
    FakeCopilotSession,
    ToolSchemaFakeTools,
    call_tool,
    make_started_adapter,
    requires_copilot_sdk,
    run_event,
)
from tests.framework_conformance.outcomeprobes import REPLY, ToolCall
from tests.runtime.customtools.kit import DEPLOY, SkipInput, recording_deploy, skip

pytestmark = requires_copilot_sdk

if _COPILOT_SDK_AVAILABLE:
    from copilot import ToolResult


class TestDeclinedTurn:
    @pytest.mark.parametrize(
        "later", [REPLY, ToolCall(DEPLOY)], ids=["reply", "custom-act"]
    )
    async def test_a_later_tool_call_is_refused_without_running(
        self, later: ToolCall
    ) -> None:
        deploy, deployed = recording_deploy()
        results: list[ToolResult] = []

        async def decline_then_call(session: FakeCopilotSession) -> None:
            await call_tool(session, DECLINE)
            results.append(await call_tool(session, later))

        client = FakeCopilotClient(turn_events=[decline_then_call])
        adapter = await make_started_adapter(
            client, additional_tools=[(SkipInput, skip), deploy]
        )
        tools = ToolSchemaFakeTools()

        await run_event(adapter, tools)

        assert results == [refused()]
        assert deployed == []
        assert tools.tool_calls == []

    async def test_a_question_after_a_decline_posts_nothing(self) -> None:
        answers: list[Any] = []

        async def decline_then_ask(session: FakeCopilotSession) -> None:
            await call_tool(session, DECLINE)
            handler = session.kwargs["on_user_input_request"]
            answers.append(await handler(QUESTION, {"session_id": session.session_id}))

        client = FakeCopilotClient(turn_events=[decline_then_ask])
        adapter = await make_started_adapter(
            client, ask_user=ASK_USER_ROOM, additional_tools=[(SkipInput, skip)]
        )
        tools = ToolSchemaFakeTools()

        await run_event(adapter, tools)

        assert answers == [freeform_answer(DECLINED_TURN_REFUSAL)]
        assert tools.chat == []
