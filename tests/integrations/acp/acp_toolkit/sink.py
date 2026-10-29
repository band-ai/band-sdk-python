"""Turn sink double for tests that observe only a session's published chunks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from band.integrations.acp.types import ACPToolCall, CollectedChunk


@dataclass(frozen=True)
class NarrationSink:
    """A ``TurnSink`` that hands each published chunk to ``post``; the tool
    activity it is told about is outside what its tests claim, so it is dropped."""

    post: Callable[[CollectedChunk], Awaitable[None]]

    async def emit(self, chunk: CollectedChunk) -> None:
        await self.post(chunk)

    async def tool_started(self, call: ACPToolCall) -> None:
        del call

    async def tool_ended(self, tool_call_id: str) -> None:
        del tool_call_id
