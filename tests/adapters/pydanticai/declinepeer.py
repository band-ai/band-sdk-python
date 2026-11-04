"""Drive native Pydantic AI model requests and custom-tool registration."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from band.runtime.custom_tools import declared_effect, declares_turn_effect
from band.runtime.tools import TurnEffect

CUSTOM = "customdecline"
SUCCESS = "settled"
FAILURE = "decline rejected"


class CustomDeclineInput(BaseModel):
    """Settle this input without a reply."""

    reason: str


Handler = Callable[[CustomDeclineInput], Awaitable[Any]]


def portable_registration(handler: Handler) -> Any:
    return CustomDeclineInput, handler


def native_registration(handler: Handler) -> Any:
    @declares_turn_effect(declared_effect(handler) or TurnEffect.OBSERVE)
    async def customdecline(reason: str) -> Any:
        return await handler(CustomDeclineInput(reason=reason))

    return customdecline


class ScriptedModel:
    def __init__(self, *batches: list[tuple[str, dict[str, Any]]]) -> None:
        self.pending = deque(batches)
        self.requests: list[list[ModelMessage]] = []
        self.model = FunctionModel(stream_function=self.stream)

    async def stream(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        self.requests.append(list(messages))
        batch = self.pending.popleft()
        if batch:
            yield {
                index: DeltaToolCall(
                    name=name,
                    json_args=json.dumps(arguments),
                    tool_call_id=f"call-{len(self.requests)}-{index}",
                )
                for index, (name, arguments) in enumerate(batch)
            }
        else:
            yield ""


def tool_results(messages: list[ModelMessage]) -> list[tuple[str, str, Any, str]]:
    return [
        (part.tool_name, part.tool_call_id, part.content, part.outcome)
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
