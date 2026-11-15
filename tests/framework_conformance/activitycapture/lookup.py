"""Hold the external lookup response before an activity invocation is ready."""

from __future__ import annotations

import asyncio

import httpx

from tests.e2e.baseline.toolkit.activitywork import LOOKUP_PATH


class HeldLookupPeer:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.requests: list[str] = []

    async def respond(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path == LOOKUP_PATH
        self.requests.append(request.url.path)
        self.entered.set()
        await self.release.wait()
        return httpx.Response(200, json={"name": "lookup-result"})
