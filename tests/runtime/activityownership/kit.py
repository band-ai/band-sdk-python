from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Awaitable
from typing import Any, Literal

import httpx

from band.platform.activity import API_VERSION_HEADER
from tests.activitysupport import SUPPORTED_VERSION
from tests.runtime.helpers import ALICE, LifecyclePlatform, page

REASONING_BODY = {"working": True, "activity": {"kind": "reasoning"}}
PERMISSION_BODY = {"working": True, "activity": {"kind": "permission"}}
MEMORY_BODY = {"working": True, "activity": {"kind": "memory"}}
BOOLEAN_BODY = {"working": True}
IDLE_BODY = {"working": False}


def tool_body(name: str) -> dict[str, Any]:
    return {"working": True, "activity": {"kind": "tool_call", "name": name}}


class ActivityPlatform(LifecyclePlatform):
    """Activity posts advertise a detail-supporting version; queued responses
    answer them before the default echo."""

    def __init__(self) -> None:
        super().__init__()
        self.activity_responses: deque[httpx.Response] = deque()
        self.activity_bodies: list[dict[str, Any]] = []
        self.hold_next_activity = False
        self.activity_entered = asyncio.Event()
        self.activity_release = asyncio.Event()
        self._reported = asyncio.Condition()

    async def respond(self, request: httpx.Request) -> httpx.Response:
        response = self.answer(request)
        if request.url.path.endswith("/activity"):
            if self.activity_responses:
                response = self.activity_responses.popleft()
            response.headers[API_VERSION_HEADER] = SUPPORTED_VERSION
            async with self._reported:
                self.activity_bodies.append(json.loads(request.content))
                self._reported.notify_all()
            if self.hold_next_activity:
                self.hold_next_activity = False
                self.activity_entered.set()
                await self.activity_release.wait()
        return response

    async def bodies_reach(self, count: int) -> list[dict[str, Any]]:
        async with self._reported:
            await self._reported.wait_for(lambda: len(self.activity_bodies) >= count)
        return self.activity_bodies

    async def shown(self, observation: Awaitable[None]) -> None:
        """Make a visible change and wait until the platform has received it."""
        count = len(self.activity_bodies)
        await observation
        await self.bodies_reach(count + 1)

    def answer(self, request: httpx.Request) -> httpx.Response:
        tail = request.url.path.rsplit("/", 1)[-1]
        if tail == "participants":
            return page([{**ALICE, "role": "member", "status": "active"}])
        if request.method == "POST" and tail == "messages":
            self.posts.append(tail)
            return httpx.Response(
                201, json={"data": {"id": "m-1", "success": True, "recipients": []}}
            )
        return super().answer(request)


class ObservedIdle(asyncio.Event):
    """The idle event a graceful stop waits on, announcing that wait."""

    def __init__(self) -> None:
        super().__init__()
        self.set()
        self.awaited = asyncio.Event()

    async def wait(self) -> Literal[True]:
        self.awaited.set()
        return await super().wait()
