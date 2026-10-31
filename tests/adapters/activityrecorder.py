"""Observe when a particular provider operation ends."""

from __future__ import annotations

import asyncio
from collections.abc import Hashable

from band.testing.activity import ActivityRecorder


class EndNotifyingRecorder(ActivityRecorder):
    def __init__(self, key: Hashable) -> None:
        super().__init__()
        self.key = key
        self.ended = asyncio.Event()
        self.release = asyncio.Event()

    async def end(self, key: Hashable) -> None:
        await super().end(key)
        if key == self.key:
            self.ended.set()
            await self.release.wait()
