"""Shared doubles for message-turn admission tests."""

from __future__ import annotations

import asyncio
from typing import Any

from band.core.simple_adapter import SimpleAdapter
from band.platform.event import MessageEvent
from band.runtime.execution import ExecutionContext
from band.runtime.originals import is_synthetic
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import LifecyclePlatform, synthetic_message

APPROVAL = "approve"


class TurnRecorder(SimpleAdapter[Any]):
    """Records every handled turn; clearing ``release`` holds the handler."""

    def __init__(self) -> None:
        super().__init__()
        self.completed: list[str] = []
        self._completions: asyncio.Queue[str] = asyncio.Queue()
        self.controls: list[str] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def try_handle_control(self, msg: Any, tools: Any) -> bool:
        if msg.content != APPROVAL:
            return False
        self.controls.append(msg.id)
        return True

    async def on_message(self, msg: Any, tools: Any, *args: Any, **kwargs: Any) -> None:
        self.entered.set()
        await self.release.wait()
        self.completed.append(msg.id)
        tools.turn.settle()
        self._completions.put_nowait(msg.id)

    async def next_completion(self) -> str:
        return await self._completions.get()


def queued_synthetic(ctx: ExecutionContext) -> list[str]:
    return [
        event.payload.id
        for event in ctx.scheduler.pending
        if isinstance(event, MessageEvent)
        and event.payload is not None
        and is_synthetic(event)
    ]


async def started(ctx: ExecutionContext) -> None:
    """Start the room loop and wait out its startup /next synchronization."""
    await ctx.start()
    await wait_for_condition(lambda: ctx.scheduler.sync_complete)


async def queue_while_stopped(
    ctx: ExecutionContext, platform: LifecyclePlatform, *message_ids: str
) -> None:
    """Stop a started room and let its loop queue synthetic work for PLAY."""
    stop_room(ctx, platform)
    for message_id in message_ids:
        await ctx.on_event(synthetic_message(message_id))
    await wait_for_condition(lambda: queued_synthetic(ctx) == list(message_ids))


def stop_room(ctx: ExecutionContext, platform: LifecyclePlatform) -> None:
    platform.stopped = True
    ctx.stop_room()


async def play(ctx: ExecutionContext, platform: LifecyclePlatform) -> None:
    platform.stopped = False
    await ctx.resume_room()


async def reconcile_once(ctx: ExecutionContext, platform: LifecyclePlatform) -> None:
    """Run one scheduled pass; its /next read follows the synthetic drain."""
    reads = len(platform.next_reads)
    await ctx.request_resync()
    await wait_for_condition(lambda: len(platform.next_reads) > reads)


def turn_settled(ctx: ExecutionContext) -> bool:
    return ctx.current_scope is not None and ctx.current_scope.settled
