"""Batch-input probes for the in-process tool-loop adapters; see ``batchprobes``."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from anthropic.types import Message, TextBlock, Usage

from band.adapters.anthropic import AnthropicAdapter
from band.core.types import AgentInput
from tests.framework_conformance.batchprobes import BatchProbe, deliver


def flattened(messages: list[dict[str, Any]]) -> str:
    """Every message the provider received, in order, as one string."""
    return "\n".join(
        message["content"]
        if isinstance(message["content"], str)
        else json.dumps(message["content"])
        for message in messages
    )


async def run_anthropic(inp: AgentInput, final_text: str) -> str:
    reply = Message(
        id="msg",
        type="message",
        role="assistant",
        model="scripted",
        content=[TextBlock(type="text", text=final_text)],
        stop_reason="end_turn",
        usage=Usage(input_tokens=0, output_tokens=0),
    )
    received: list[dict[str, Any]] = []

    async def create(**request: Any) -> Message:
        received[:] = request["messages"]
        return reply

    adapter = AnthropicAdapter()
    adapter.client = cast(
        "Any",
        SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=create))),
    )
    await adapter.on_started("Agent", "A test agent")
    await deliver(adapter, inp)
    return flattened(received)


PROBES: dict[str, BatchProbe] = {"anthropic": BatchProbe(run=run_anthropic)}
