"""Native chat model and tool outcomes for explicit-decline regressions."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from enum import StrEnum
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.outputs import ChatResult
from pydantic import BaseModel, Field

from band.runtime.tools import BandTool
from band.testing import FakeAgentTools


class DeclineFailure(StrEnum):
    INVALID_ARGUMENTS = "invalid-decline"
    RAISED_DECLINE = "raised-decline"
    STRUCTURED_DECLINE = "structured-decline"
    FAILED_SIBLING = "failed-sibling"


class ScriptedModel(GenericFakeChatModel):
    requests: int = 0
    inputs: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedModel:
        return self

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        self.inputs.append(deepcopy(args[0]))
        self.requests += 1
        return super()._generate(*args, **kwargs)


class DeclineTools(FakeAgentTools):
    def __init__(self, failure: DeclineFailure | None = None) -> None:
        super().__init__()
        self.failure = failure

    @property
    def replies(self) -> list[str]:
        return [
            call["arguments"]["content"]
            for call in self.tool_calls
            if call["tool_name"] == BandTool.SEND_MESSAGE
        ]

    async def execute_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        match (self.failure, tool_name):
            case (DeclineFailure.RAISED_DECLINE, BandTool.NO_REPLY):
                raise RuntimeError("decline rejected")
            case (DeclineFailure.STRUCTURED_DECLINE, BandTool.NO_REPLY):
                return {"ok": False}
            case (DeclineFailure.FAILED_SIBLING, BandTool.GET_PARTICIPANTS):
                raise RuntimeError("sibling rejected")
        return await super().execute_tool_call(tool_name, arguments)


CUSTOM = "customdecline"
REASON = "FYI"
SUCCESS = "settled"
FAILURE = "decline rejected"


class CustomDeclineInput(BaseModel):
    """Settle an input without replying."""

    reason: str


def tool_response(
    name: str = CUSTOM,
    *,
    arguments: dict[str, Any] | None = None,
    call_id: str = "decline",
) -> dict[str, Any]:
    return {
        "name": name,
        "args": {"reason": REASON} if arguments is None else arguments,
        "id": call_id,
    }


def tool_results(messages: list[BaseMessage]) -> list[tuple[str | None, str, Any, str]]:
    return [
        (message.name, message.tool_call_id, message.content, message.status)
        for message in messages
        if isinstance(message, ToolMessage)
    ]
