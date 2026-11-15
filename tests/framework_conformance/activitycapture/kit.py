"""A real activity HTTP transport whose response can be held."""

from __future__ import annotations

import asyncio

import httpx

from tests.platform.activity.kit import ActivityPeer


class HeldActivityPeer(ActivityPeer):
    def __init__(self) -> None:
        super().__init__()
        self.hold = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def respond(self, request: httpx.Request) -> httpx.Response:
        response = await super().respond(request)
        if self.hold:
            self.entered.set()
            await self.release.wait()
        return response
