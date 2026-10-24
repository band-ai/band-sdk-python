from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Awaitable
from typing import Any, Literal

import httpx

from band.core.turn_lifecycle import TurnTask, bind_turn_task
from band.platform.activity import API_VERSION_HEADER
from band.platform.event import MessageEvent, PlatformEvent
from band.runtime.activity import set_permission_wait
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from band.runtime.toolactivity import (
    end_tool_activity,
    report_memory_activity,
    report_tool_activity,
)
from band.runtime.tools.agent import AgentTools
from tests.activitysupport import SUPPORTED_VERSION
from tests.runtime.helpers import ALICE, LifecyclePlatform, is_message_post, page

CONTROL_MESSAGE_ID = "approve"


def recognizes_control(ctx: ExecutionContext, event: MessageEvent) -> bool:
    return event.payload.id == CONTROL_MESSAGE_ID


async def deliver_event(ctx: ExecutionContext, event: PlatformEvent) -> None:
    """Run queued input through the consumer's production scheduling pass."""
    await ctx.on_event(event)
    await ctx.scheduler.run_pass()


def message_content(request: httpx.Request) -> str:
    return json.loads(request.content)["message"]["content"]


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
        self.room_messages: list[str] = []
        self.activity_responses: deque[httpx.Response] = deque()
        self.activity_bodies: list[dict[str, Any]] = []
        self.hold_next_activity = False
        self.activity_entered = asyncio.Event()
        self.activity_release = asyncio.Event()
        self.late_changes: list[tuple[dict[str, Any], float]] = []
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

    async def body_at(self, index: int) -> dict[str, Any]:
        await self.bodies_reach(index + 1)
        return self.activity_bodies[index]

    async def next_body(self) -> dict[str, Any]:
        """Wait for the platform to receive one more activity body."""
        return await self.body_at(len(self.activity_bodies))

    async def shown(self, observation: Awaitable[None]) -> None:
        """Make a visible change and wait until the platform has received it.

        Assumes looptime: under real time every change would look late. A body
        that arrives only after virtual time passed came from a keep-alive, not
        the change; it is recorded with that delay in ``late_changes`` rather
        than raised, because a handler would swallow the error as a failed turn.
        """
        loop = asyncio.get_running_loop()
        index = len(self.activity_bodies)
        observed_at = loop.time()
        await observation
        body = await self.body_at(index)
        if waited := loop.time() - observed_at:
            self.late_changes.append((body, waited))

    def answer(self, request: httpx.Request) -> httpx.Response:
        tail = request.url.path.rsplit("/", 1)[-1]
        if tail == "participants":
            return page([{**ALICE, "role": "member", "status": "active"}])
        if is_message_post(request):
            self.posts.append(tail)
            self.room_messages.append(message_content(request))
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


class IdleReportGate:
    """Holds the room's ``working: false`` report while ``release`` is clear."""

    def __init__(self, peer: ActivityPlatform) -> None:
        self.peer = peer
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def answer(self, request: httpx.Request) -> httpx.Response:
        response = await self.peer.respond(request)
        if (
            request.url.path.endswith("/activity")
            and not json.loads(request.content)["working"]
        ):
            self.entered.set()
            await self.release.wait()
        return response


class HeldWorkerHandler:
    """A message handler whose every turn binds one worker held on ``release``."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.workers: list[TurnTask] = []

    async def __call__(self, ctx: ExecutionContext, event: PlatformEvent) -> None:
        worker = asyncio.create_task(self.release.wait())
        self.workers.append(bind_turn_task(AgentTools.from_context(ctx), worker))

    async def finish(self, ctx: ExecutionContext) -> None:
        """Release every worker and join the activity report their end starts."""
        self.release.set()
        await asyncio.gather(*(worker.task for worker in self.workers))
        await asyncio.sleep(0)
        await ctx._join_activity_completion()


async def consume_control(ctx: ExecutionContext, event: PlatformEvent) -> bool:
    return True


class HeldReplyPlatform(ActivityPlatform):
    """Holds the post of one reply until ``release`` is set, so its turn can be
    kept in the delivery tail after the model has finished."""

    def __init__(self, held_reply: str) -> None:
        super().__init__()
        self.held_reply = held_reply
        self.reply_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def respond(self, request: httpx.Request) -> httpx.Response:
        if is_message_post(request) and message_content(request) == self.held_reply:
            self.reply_entered.set()
            await self.release.wait()
        return await super().respond(request)
