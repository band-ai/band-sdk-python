"""Preprocessor policies sharing one read-only message projection."""

from __future__ import annotations

from dataclasses import replace

from band.core.types import AgentInput, PlatformMessage
from band.platform.event import MessageEvent, PlatformEvent
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.execution import ExecutionContext


class ControlPolicy:
    def __init__(self, rewritten: str | None) -> None:
        self.rewritten = rewritten
        self.default = DefaultPreprocessor()
        self.seen: list[str] = []

    def message(
        self, ctx: ExecutionContext, event: PlatformEvent, agent_id: str
    ) -> PlatformMessage | None:
        message = self.default.message(ctx, event, agent_id)
        if message is None or self.rewritten is None:
            return None
        return replace(message, content=self.rewritten)

    async def process(
        self, ctx: ExecutionContext, event: PlatformEvent, agent_id: str
    ) -> AgentInput | None:
        if isinstance(event, MessageEvent) and event.payload is not None:
            self.seen.append(event.payload.id)
        message = self.message(ctx, event, agent_id)
        if message is None:
            return None
        inp = await self.default.process(ctx, event, agent_id)
        return replace(inp, msg=message) if inp else None
