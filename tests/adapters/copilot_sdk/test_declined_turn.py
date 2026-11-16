"""Once the model declines, Copilot's later tool calls and questions do nothing."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE
from band.integrations.copilot_sdk import ASK_USER_ROOM
from band.integrations.copilot_sdk.room_ask_user import freeform_answer
from band.runtime.custom_tools import declares_turn_effect, get_custom_tool_name
from band.runtime.tools import TurnEffect
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from tests.adapters.copilot_sdk.fakes import (
    QUESTION,
    FakeCopilotClient,
    FakeCopilotSession,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_event,
)
from tests.framework_conformance.outcomeprobes import REPLY, ToolCall

pytestmark = requires_copilot_sdk

if _COPILOT_SDK_AVAILABLE:
    from copilot import ToolInvocation, ToolResult


class SkipInput(BaseModel):
    """Decline to reply to this message."""


class DeployInput(BaseModel):
    """Deploy the service."""


@declares_turn_effect(TurnEffect.DECLINE)
async def skip(args: SkipInput) -> str:
    return "skipped"


DECLINE = ToolCall(get_custom_tool_name(SkipInput))
DEPLOY = ToolCall(get_custom_tool_name(DeployInput))


async def call_tool(session: FakeCopilotSession, call: ToolCall) -> ToolResult:
    """The model calling ``call`` through the session's registered tool."""
    return await session.find_tool(call.name).handler(
        ToolInvocation(
            tool_call_id=f"call-{call.name}",
            tool_name=call.name,
            arguments=call.arguments,
        )
    )


class TestDeclinedTurn:
    @pytest.mark.parametrize("later", [REPLY, DEPLOY], ids=["reply", "custom-act"])
    async def test_a_later_tool_call_is_refused_without_running(
        self, later: ToolCall
    ) -> None:
        deployed: list[DeployInput] = []

        @declares_turn_effect(TurnEffect.ACT)
        async def deploy(args: DeployInput) -> str:
            deployed.append(args)
            return "deployed"

        results: list[ToolResult] = []

        async def decline_then_call(session: FakeCopilotSession) -> None:
            await call_tool(session, DECLINE)
            results.append(await call_tool(session, later))

        client = FakeCopilotClient(turn_events=[decline_then_call])
        adapter = await make_started_adapter(
            client, additional_tools=[(SkipInput, skip), (DeployInput, deploy)]
        )
        tools = ToolSchemaFakeTools()

        await run_event(adapter, tools)

        assert results == [
            ToolResult(
                text_result_for_llm=DECLINED_TURN_REFUSAL,
                result_type="failure",
                error=DECLINED_TURN_REFUSAL,
            )
        ]
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
