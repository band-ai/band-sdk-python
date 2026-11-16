"""Kit for Copilot declined-turn tests: a declining custom tool, the model's
calls through the session's registered tools, and room tools that post."""

from __future__ import annotations

from pydantic import BaseModel

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE
from band.runtime.custom_tools import declares_turn_effect, get_custom_tool_name
from band.runtime.tools import TurnEffect
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from tests.adapters.copilot_sdk.fakes import FakeCopilotSession, ToolSchemaFakeTools
from tests.framework_conformance.turnprobes import DispatchingFakeTools, ToolCall

if _COPILOT_SDK_AVAILABLE:
    from copilot import ToolInvocation, ToolResult


class SkipInput(BaseModel):
    """Decline to reply to this message."""


@declares_turn_effect(TurnEffect.DECLINE)
async def skip(args: SkipInput) -> str:
    return "skipped"


DECLINE = ToolCall(get_custom_tool_name(SkipInput))


def refused() -> ToolResult:
    """The failed result the model receives for a refused call."""
    return ToolResult(
        text_result_for_llm=DECLINED_TURN_REFUSAL,
        result_type="failure",
        error=DECLINED_TURN_REFUSAL,
    )


class PostingTools(DispatchingFakeTools, ToolSchemaFakeTools):
    """Room tools advertising the Band reply tool, whose dispatched calls post."""


async def call_tool(
    session: FakeCopilotSession, call: ToolCall, *, agent_id: str | None = None
) -> ToolResult:
    """The model, or its sub-agent ``agent_id``, calling ``call`` through the
    session's registered tool."""
    return await session.find_tool(call.name).handler(
        ToolInvocation(
            tool_call_id=f"call-{call.name}",
            tool_name=call.name,
            arguments=call.arguments,
            agent_id=agent_id,
        )
    )
