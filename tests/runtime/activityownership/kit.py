from __future__ import annotations

import asyncio
import json

import httpx

from band.core.turn_lifecycle import TurnTask, bind_turn_task
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.runtime.helpers import ALICE, LifecyclePlatform, is_message_post, page


def message_content(request: httpx.Request) -> str:
    return json.loads(request.content)["message"]["content"]


class ActivityPlatform(LifecyclePlatform):
    """A running room whose participants are listed and whose messages land."""

    def __init__(self) -> None:
        super().__init__()
        self.room_messages: list[str] = []

    async def respond(self, request: httpx.Request) -> httpx.Response:
        return self.answer(request)

    def answer(self, request: httpx.Request) -> httpx.Response:
        tail = request.url.path.rsplit("/", 1)[-1]
        if tail == "participants":
            return page([{**ALICE, "role": "member", "status": "active"}])
        if is_message_post(request):
            self.room_messages.append(message_content(request))
            return httpx.Response(
                201, json={"data": {"id": "m-1", "success": True, "recipients": []}}
            )
        return super().answer(request)


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
        return self.answer(request)
