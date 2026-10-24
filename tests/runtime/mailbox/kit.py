"""Holds that pause a mailbox job until the test releases it."""

from __future__ import annotations

import asyncio


class JobHold:
    """One pause point: the job sets ``entered``, then waits for ``release``."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    def hold(self) -> None:
        self.entered.clear()
        self.release.clear()

    async def pause(self) -> None:
        self.entered.set()
        await self.release.wait()
