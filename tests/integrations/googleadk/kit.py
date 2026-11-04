"""Script the external Google client while retaining native ADK execution."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from copy import deepcopy
from typing import Any

from google.genai import types
from pydantic import BaseModel

from band.core.types import MessageType
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools.schema import ToolCallOutcome
from band.runtime.tools.types import BandTool, TurnEffect
from band.testing import FakeAgentTools, events_of_type
from tests.adapters.genaikit import PlatformSchemaFakeTools, text_reply

DECLINED = "Silence selected"
FOLLOWUP = "Unexpected followup"
DECLINE_REJECTED = "Decline rejected"
WORK_REJECTED = "Work rejected"
HANDLER_UNAVAILABLE = "Handler unavailable"
HANDLER_REJECTED = "Handler rejected"


class DeclineInput(BaseModel):
    """Decline the current request."""


@declares_turn_effect(TurnEffect.DECLINE)
async def decline() -> str:
    return DECLINED


def batch(*calls: tuple[str, dict[str, Any]]) -> types.GenerateContentResponse:
    """Build one finalized non-streaming native function-call batch."""
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_function_call(name=name, args=args)
                        for name, args in calls
                    ],
                ),
                finish_reason=types.FinishReason.STOP,
            )
        ]
    )


class GoogleModelPeer:
    """Return only explicitly queued responses and retain model requests."""

    def __init__(self) -> None:
        self.responses: deque[types.GenerateContentResponse] = deque()
        self.requests: list[Any] = []

    async def generate(
        self, *, model: str, contents: Any, config: Any = None
    ) -> types.GenerateContentResponse:
        self.requests.append(deepcopy(contents))
        assert self.responses, "Unexpected model request"
        return self.responses.popleft()

    def decline_then_followup(self) -> None:
        self.responses.extend([batch(("decline", {})), text_reply(FOLLOWUP)])


class WorkInput(BaseModel):
    """Complete ordinary sibling work."""

    value: int = 1


class GatedWork:
    """Hold a real handler at entry until its owner releases it."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.values: list[int] = []

    async def __call__(self, args: WorkInput) -> dict[str, Any]:
        self.entered.set()
        await self.release.wait()
        self.values.append(args.value)
        return {"value": args.value}


def result_payloads(tools: FakeAgentTools) -> list[dict[str, Any]]:
    """Read emitted native tool results."""
    return [
        json.loads(event["content"])
        for event in events_of_type(tools, MessageType.TOOL_RESULT)
    ]


def model_results(
    contents: list[types.Content],
) -> list[tuple[str | None, dict[str, Any] | None]]:
    """Read actual function responses presented to a subsequent model request."""
    return [
        (part.function_response.name, part.function_response.response)
        for content in contents
        for part in content.parts or []
        if part.function_response
    ]


class FailedPlatformTools(PlatformSchemaFakeTools):
    """Expose a rejected platform decline without recording a successful effect."""

    async def execute_tool_call_structured(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolCallOutcome:
        assert tool_name == BandTool.NO_REPLY
        self.tool_calls.append({"tool_name": tool_name, "arguments": arguments})
        return ToolCallOutcome(value=DECLINE_REJECTED, ok=False)
