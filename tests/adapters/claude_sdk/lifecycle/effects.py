"""A custom tool whose actual side effect can be held past native process exit."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel


class HeldEffectInput(BaseModel):
    """Perform an externally controlled effect."""


class HeldEffect:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def __call__(self, args: HeldEffectInput) -> str:
        self.started.set()
        await self.release.wait()
        self.finished.set()
        return "effect completed"
