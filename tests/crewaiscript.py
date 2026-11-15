"""A scripted CrewAI model and the native tool calls it answers with."""

from __future__ import annotations

import json
from collections import deque
from typing import Any

from crewai.llms.base_llm import BaseLLM

from tests.framework_conformance.turnprobes import ToolCall


def native_tool_call(
    call_id: str, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """One function call as CrewAI's native tool path parses it."""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def step(*calls: ToolCall) -> list[dict[str, Any]]:
    """One model response making ``calls`` together, in order."""
    return [
        native_tool_call(f"call-{index}", str(call.name), call.arguments)
        for index, call in enumerate(calls)
    ]


class ScriptedLLM(BaseLLM):
    """Answers each model call with the next queued response, raising a queued
    exception; a call past the script fails instead of answering."""

    def __init__(self, *responses: Any, function_calling: bool = True) -> None:
        super().__init__(model="scripted-crewai-model")
        self.responses: deque[Any] = deque(responses)
        self.asked = 0
        self.function_calling = function_calling
        self.messages: list[Any] = []

    def get_context_window_size(self) -> int:
        """Keep the complete Band tool surface within the scripted model context."""
        return 128_000

    def supports_function_calling(self) -> bool:
        return self.function_calling

    def call(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        self.messages.append(messages)
        self.asked += 1
        if not self.responses:
            raise AssertionError(f"CrewAI asked for unscripted model call {self.asked}")
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    async def acall(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        return self.call(messages, *args, **kwargs)


def react_step(call: ToolCall) -> str:
    """One action in CrewAI 1.15.30's text ReAct parser format."""
    return (
        f"Thought: Use the requested tool.\nAction: {call.name}\n"
        f"Action Input: {json.dumps(call.arguments)}"
    )
