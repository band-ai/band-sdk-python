"""Real room executions and HTTP observations for permission ownership tests."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

import httpx

from band.core.turn_lifecycle import TurnTask, bind_turn_task
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.runtime.activityownership.kit import ActivityPlatform


def fail_resolution(task: asyncio.Task[None], result: asyncio.Future[None]) -> None:
    result.set_exception(ValueError("approval failed"))


def cancel_resolution(task: asyncio.Task[None], result: asyncio.Future[None]) -> None:
    task.cancel()


class RoomActivityPlatform(ActivityPlatform):
    """Keep room routing visible at the real activity HTTP boundary."""

    def __init__(self) -> None:
        super().__init__()
        self.room_bodies: dict[str, list[dict[str, Any]]] = defaultdict(list)

    async def respond(self, request: httpx.Request) -> httpx.Response:
        response = await super().respond(request)
        if request.url.path.endswith("/activity"):
            room_id = request.url.path.split("/")[-2]
            self.room_bodies[room_id].append(json.loads(request.content))
        return response

    async def checkpoint(self, ctx: ExecutionContext) -> dict[str, Any]:
        """Run the reporter's actual HTTP callback, without waiting for a timer."""
        await ctx._report_working_state(True)
        return self.room_bodies[ctx.room_id][-1]


class HeldTurn:
    """A real dispatched turn retained by its bound execution worker."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.workers: list[TurnTask] = []
        self.tools: list[AgentTools] = []

    async def handle(self, ctx: ExecutionContext, event: PlatformEvent) -> None:
        self.tools.extend((AgentTools.from_context(ctx), AgentTools.from_context(ctx)))
        self.workers.append(
            bind_turn_task(self.tools[-1], asyncio.create_task(self.release.wait()))
        )

    async def finish(self, ctx: ExecutionContext) -> None:
        self.release.set()
        await asyncio.gather(*(worker.task for worker in self.workers))
        await ctx._join_activity_completion()
