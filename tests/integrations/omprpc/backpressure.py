"""Observe real subprocess pipe saturation without replacing writes or drains."""

from __future__ import annotations

import asyncio
from typing import Any

from band.integrations.ompwire import OMP_FRAME_BYTES

PRESSURE_WRITE_COUNT = 8


class BackpressureProtocol(asyncio.subprocess.SubprocessStreamProtocol):
    def __init__(
        self, limit: int, loop: asyncio.AbstractEventLoop, paused: asyncio.Event
    ) -> None:
        super().__init__(limit, loop)
        self.paused = paused

    def pause_writing(self) -> None:
        super().pause_writing()
        self.paused.set()


class BackpressureSpawn:
    def __init__(self) -> None:
        self.paused = asyncio.Event()

    async def create(
        self, *command: str, limit: int = OMP_FRAME_BYTES, **kwargs: Any
    ) -> asyncio.subprocess.Process:
        loop = asyncio.get_running_loop()
        protocol = BackpressureProtocol(limit, loop, self.paused)
        transport, _ = await loop.subprocess_exec(lambda: protocol, *command, **kwargs)
        return asyncio.subprocess.Process(transport, protocol, loop)
