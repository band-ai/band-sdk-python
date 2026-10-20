"""Observable child finalizers at the ACP request boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class HeldFinalizer:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.closing = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()

    async def run(self, *args: Any, **kwargs: Any) -> None:
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.closing.set()
            await self.release.wait()
            self.closed.set()


@asynccontextmanager
async def held_finalizer() -> AsyncIterator[HeldFinalizer]:
    finalizer = HeldFinalizer()
    try:
        yield finalizer
    finally:
        finalizer.release.set()
