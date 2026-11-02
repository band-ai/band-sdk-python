"""Kit: a scripted CrewAI model, its tool-call steps, and a custom action."""

from __future__ import annotations

from collections import deque
from typing import Any

from crewai.llms.base_llm import BaseLLM
from pydantic import BaseModel

from band.runtime.custom_tools import get_custom_tool_name
from tests.crewaicalls import native_tool_call
from tests.framework_conformance.turnprobes import ToolCall


class ScriptedLLM(BaseLLM):
    """Returns each queued response in turn, raising a queued exception."""

    def __init__(self) -> None:
        super().__init__(model="offline-completion-test")
        self.responses: deque[Any] = deque()

    def supports_function_calling(self) -> bool:
        return True

    def call(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    async def acall(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        return self.call(messages, *args, **kwargs)


class WorkInput(BaseModel):
    """Perform an action."""

    fail: bool = False


def step(call: ToolCall) -> list[dict[str, Any]]:
    """One model step making ``call``."""
    return [native_tool_call("call-offline", str(call.name), call.arguments)]


def work(*, fail: bool) -> ToolCall:
    """A call to the custom action, failing when ``fail``."""
    return ToolCall(get_custom_tool_name(WorkInput), {"fail": fail})
