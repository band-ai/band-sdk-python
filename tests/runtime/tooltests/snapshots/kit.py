"""Gated REST membership peer and context fixtures for snapshot tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from band.client.rest import AsyncRestClient
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig

ROOM = "snapshot-room"
ALICE = {
    "id": "alice",
    "name": "Alice",
    "type": "User",
    "role": "member",
    "status": "active",
}
BOB = {"id": "bob", "name": "Bob", "type": "User", "role": "member", "status": "active"}
AGENT = {
    "id": "agent",
    "name": "Agent",
    "type": "Agent",
    "role": "member",
    "status": "active",
}


async def ignore_event(*args: Any) -> None:
    """Leave execution events untouched."""


class SnapshotPeer:
    """Serve fresh HTTP roster responses, optionally holding one read."""

    def __init__(self, body: Any, *, status: int = 200, blocked: bool = False) -> None:
        self.body = body
        self.status = status
        self.arrived = asyncio.Event()
        self.release = asyncio.Event()
        self.requests: list[httpx.Request] = []
        if not blocked:
            self.release.set()

    async def answer(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.endswith(f"/{ROOM}/participants")
        self.requests.append(request)
        self.arrived.set()
        await self.release.wait()
        return httpx.Response(self.status, json=self.body)

    @asynccontextmanager
    async def context(self) -> AsyncIterator[ExecutionContext]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(self.answer)
        ) as http:
            rest = AsyncRestClient(
                api_key="test-key", base_url="https://snapshot.test", httpx_client=http
            )
            link = BandLink(agent_id=AGENT["id"], api_key="test-key")
            link.rest = rest
            ctx = ExecutionContext(
                room_id=ROOM,
                link=link,
                on_execute=ignore_event,
                config=SessionConfig(enable_context_hydration=False),
            )
            ctx.set_participants([ALICE, AGENT])
            yield ctx
