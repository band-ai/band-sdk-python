"""Strict scripted model peer for native Strands batch boundaries."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from copy import deepcopy
from typing import Any

from pydantic import BaseModel
from strands.types.content import Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec

from band.testing.strands import ScriptedStrandsModel, ScriptedTurn, ToolTurn


class DeclineModel(ScriptedStrandsModel):
    """Record requests and reject unplanned calls, preserving native execution."""

    def __init__(self, turns: Sequence[ScriptedTurn | tuple[ToolTurn, ...]]) -> None:
        super().__init__([])
        self.turns = list(turns)
        self.requests: list[Messages] = []

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        self.requests.append(deepcopy(messages))
        if not self.turns:
            raise AssertionError("Unscripted model request")
        turn = self.turns.pop(0)
        if not isinstance(turn, tuple):
            model = ScriptedStrandsModel([turn])
            async for event in model.stream(
                messages, tool_specs, system_prompt, **kwargs
            ):
                yield event
            return
        yield {"messageStart": {"role": "assistant"}}
        for call in turn:
            model = ScriptedStrandsModel([call])
            async for event in model.stream(
                messages, tool_specs, system_prompt, **kwargs
            ):
                if "messageStart" not in event and "messageStop" not in event:
                    yield event
        yield {"messageStop": {"stopReason": "tool_use"}}


def results(messages: Messages) -> list[dict[str, Any]]:
    """Project every completed native result, including status and content."""
    return [
        block["toolResult"]
        for message in messages
        for block in message["content"]
        if "toolResult" in block
    ]


class DeclineInput(BaseModel):
    """Decline a request with a note."""

    note: str
