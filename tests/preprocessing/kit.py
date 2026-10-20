from __future__ import annotations

from dataclasses import replace

from band.core.types import AgentInput
from band.platform.event import PlatformEvent
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.execution import ExecutionContext


class ControlPolicy(DefaultPreprocessor):
    def __init__(self, rewritten: str | None) -> None:
        self.rewritten = rewritten
        self.seen: list[str] = []

    async def process(
        self, ctx: ExecutionContext, event: PlatformEvent, agent_id: str
    ) -> AgentInput | None:
        inp = await super().process(ctx, event, agent_id)
        if inp is None:
            return None
        self.seen.append(inp.msg.id)
        if self.rewritten is None:
            return None
        return replace(inp, msg=replace(inp.msg, content=self.rewritten))
