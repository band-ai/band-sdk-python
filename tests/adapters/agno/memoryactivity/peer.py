"""Gated external Agno event stream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from band.testing.activity import ActivityRecorder


class MemoryStream:
    def __init__(self) -> None:
        self.events: asyncio.Queue[Any] = asyncio.Queue()
        self.consumed: asyncio.Queue[None] = asyncio.Queue()

    async def arun(self, **kwargs: Any) -> AsyncIterator[Any]:
        assert kwargs["stream"] is True
        assert kwargs["stream_events"] is True
        while True:
            event = await self.events.get()
            if isinstance(event, Exception):
                raise event
            yield event
            self.consumed.put_nowait(None)


@dataclass(frozen=True)
class MemoryTurn:
    peer: MemoryStream
    recorder: ActivityRecorder
    task: asyncio.Task[None]
