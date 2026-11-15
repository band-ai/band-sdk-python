"""Hold a real Strands loop at the provider response after native tool work."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from strands.types.content import Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec

from band.testing import TextTurn, ToolTurn
from band.testing.activity import ActivityRecorder
from band.testing.strands import ScriptedStrandsModel


class HeldFollowupModel(ScriptedStrandsModel):
    def __init__(self, name: str) -> None:
        super().__init__([ToolTurn(name, {"note": "go"}), TextTurn("finished")])
        self.followup = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        if any("toolResult" in block for block in messages[-1]["content"]):
            self.followup.set()
            await self.release.wait()
        async for event in super().stream(
            messages, tool_specs, system_prompt, **kwargs
        ):
            yield event


@dataclass(frozen=True)
class NativeActivityTurn:
    name: str
    entered: asyncio.Event
    exited: asyncio.Event
    result: asyncio.Future[str]
    recorder: ActivityRecorder
    task: asyncio.Task[None]
    model: HeldFollowupModel
