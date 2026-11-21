"""Kit for Copilot declined-turn tests: the decline the model calls, what
it receives for refused and posted calls, and room tools that post."""

from __future__ import annotations

import json
from typing import Any

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE
from band.core.types import MessageType
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from band.testing import FakeAgentTools
from tests.adapters.copilot_sdk.fakes import ToolSchemaFakeTools
from tests.framework_conformance.outcomeprobes import REPLY_SENT
from tests.framework_conformance.turnprobes import DispatchingFakeTools, ToolCall
from tests.runtime.customtools.kit import SKIP

if _COPILOT_SDK_AVAILABLE:
    from copilot import ToolResult


#: The model's custom decline, as it calls it.
CUSTOM_DECLINE = ToolCall(SKIP)


def refused() -> ToolResult:
    """The failed result the model receives for a refused call."""
    return ToolResult(
        text_result_for_llm=DECLINED_TURN_REFUSAL,
        result_type="failure",
        error=DECLINED_TURN_REFUSAL,
    )


def replied() -> ToolResult:
    """The result the model receives for the turn's first posted ``REPLY``."""
    return ToolResult(text_result_for_llm=json.dumps(REPLY_SENT), result_type="success")


class PostingTools(DispatchingFakeTools, ToolSchemaFakeTools):
    """Room tools advertising the Band reply tool, whose dispatched calls post."""


def tool_events(tools: FakeAgentTools) -> list[tuple[str, dict[str, Any]]]:
    """The reported tool calls and results, in order, as ``(type, payload)``."""
    return [
        (event["message_type"], json.loads(event["content"]))
        for event in tools.events_sent
        if event["message_type"] in {MessageType.TOOL_CALL, MessageType.TOOL_RESULT}
    ]
