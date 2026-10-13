"""A backend cancel call that records each handle it is asked to stop."""

from __future__ import annotations

import asyncio


class RecordingCancel:
    """Records every handle sent; ``hold()`` keeps the call in flight."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.finished: list[str] = []
        self.release = asyncio.Event()
        self.release.set()

    def hold(self) -> None:
        self.release.clear()

    async def __call__(self, handle: str) -> None:
        self.sent.append(handle)
        await self.release.wait()
        self.finished.append(handle)
