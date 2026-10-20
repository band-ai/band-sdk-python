from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
from anthropic.types import Message, ToolUseBlock, Usage
from pydantic import BaseModel

from tests.adapters.lettakit import make_tool_call_message, make_tool_return_message


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


class LettaActivityStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        call = make_tool_call_message("shell", '{"command":"private-argument"}')
        yield f"data: {call.model_dump_json()}\n\n".encode()
        self.entered.set()
        await self.release.wait()
        result = make_tool_return_message("shell", "private-result")
        yield f"data: {result.model_dump_json()}\n\n".encode()
        yield b'data: {"message_type":"usage_statistics","prompt_tokens":17,"completion_tokens":5,"cached_input_tokens":3,"cache_write_tokens":2}\n\n'
        yield b"data: [DONE]\n\n"

    async def aclose(self) -> None:
        self.closed = True
