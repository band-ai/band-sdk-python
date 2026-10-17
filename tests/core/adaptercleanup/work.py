"""A turn that keeps using its resource during cancellation finalization."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from tests.adaptercleanup import TeardownAdapter


class WorkingAdapter(TeardownAdapter):
    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__()
        self.client = client
        self.entered = asyncio.Event()
        self.finalizing = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def on_message(self, *args: Any, **kwargs: Any) -> None:
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.finalizing.set()
            await self.release.wait()
            response = await self.client.get("/resource")
            response.raise_for_status()
            self.finished.set()


class SuppressingWorkingAdapter(WorkingAdapter):
    async def on_message(self, *args: Any, **kwargs: Any) -> None:
        try:
            await super().on_message(*args, **kwargs)
        except asyncio.CancelledError:
            return
