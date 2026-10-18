from __future__ import annotations

from anthropic.types import Message, ToolUseBlock, Usage
from pydantic import BaseModel


class ActivityLookup(BaseModel):
    """Look up an opaque test value."""

    note: str


def anthropic_tool_response(name: str, note: str) -> Message:
    return Message(
        id="message",
        role="assistant",
        type="message",
        model="test-model",
        content=[
            ToolUseBlock(id="tool-id", name=name, type="tool_use", input={"note": note})
        ],
        usage=Usage(input_tokens=1, output_tokens=1),
        stop_reason="tool_use",
    )
