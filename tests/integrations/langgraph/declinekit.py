"""Native chat model and tool outcomes for explicit-decline regressions."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.outputs import ChatResult

from band.runtime.tools import BandTool
from band.testing import FakeAgentTools


class DeclineFailure(StrEnum):
    INVALID_ARGUMENTS = "invalid-decline"
    RAISED_DECLINE = "raised-decline"
    STRUCTURED_DECLINE = "structured-decline"
    FAILED_SIBLING = "failed-sibling"


class ScriptedModel(GenericFakeChatModel):
    requests: int = 0

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedModel:
        return self

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
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
