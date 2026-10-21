from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Awaitable
from typing import Any, Literal

import httpx

from band.platform.activity import API_VERSION_HEADER
from band.runtime.activity import (
    end_tool_activity,
    report_memory_activity,
    report_tool_activity,
    set_permission_wait,
)
from band.runtime.sessionconfig import SessionConfig
from tests.activitysupport import SUPPORTED_VERSION
from tests.runtime.helpers import ALICE, LifecyclePlatform, page

KEEP_ALIVE_SECONDS = SessionConfig().working_keep_alive_seconds


async def past_a_keep_alive() -> None:
    """Let a keep-alive fall due, so a wrongly woken reporter would have sent."""
    await asyncio.sleep(2 * KEEP_ALIVE_SECONDS)


async def report_every_kind(tools: object) -> None:
    await report_tool_activity(tools, "Bash")
    await set_permission_wait(tools, True)
    await report_memory_activity(tools)
    await set_permission_wait(tools, False)
    await end_tool_activity(tools)


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
        self.late_changes: list[float] = []
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
        """Make a visible change and wait until the platform has received it.

        A body that arrives only after virtual time passed came from a
        keep-alive, not the change; it is recorded in ``late_changes`` rather
        than raised, because a handler would swallow the error as a failed turn.
        """
        loop = asyncio.get_running_loop()
        count = len(self.activity_bodies)
        observed_at = loop.time()
        await observation
        await self.bodies_reach(count + 1)
        if waited := loop.time() - observed_at:
            self.late_changes.append(waited)

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
