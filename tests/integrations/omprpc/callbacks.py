"""Hold real RPC callback work at explicit delivery and retirement gates."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

import psutil

from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpContent,
    OmpContentKind,
    OmpEvent,
    OmpEventType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.integrations.omprpc.events import EventRecorder
from tests.integrations.ompscenarios import PeerScenario


class BlockedEventObserver(EventRecorder):
    """Hold event delivery while native completion frames arrive."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()

    async def on_event(self, event: OmpEvent) -> None:
        if event.type == OmpEventType.MESSAGE_UPDATE:
            self.entered.set()
            await self.resume.wait()
        await super().on_event(event)


class StubbornCallbackObserver(EventRecorder):
    """Hold claimed callback work until the test explicitly releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.resume = asyncio.Event()
        self.finished = asyncio.Event()
        self.retired = asyncio.Event()

    async def hold_callback(self) -> None:
        owner = asyncio.current_task()
        assert owner is not None
        owner.add_done_callback(lambda _task: self.retired.set())
        self.entered.set()
        try:
            while not self.resume.is_set():
                try:
                    await self.resume.wait()
                except asyncio.CancelledError:
                    self.cancel_seen.set()
        finally:
            self.finished.set()

    async def release(self) -> None:
        self.resume.set()
        if self.entered.is_set():
            await self.finished.wait()


class StubbornToolObserver(StubbornCallbackObserver):
    """Keep claimed host work alive until the test explicitly releases it."""

    async def on_tool(self, call: OmpHostToolCall) -> OmpToolResult:
        await self.hold_callback()
        return OmpToolResult(content=[])


class StubbornEventObserver(StubbornCallbackObserver):
    """Record callback admission while keeping its event worker alive."""

    async def on_event(self, event: OmpEvent) -> None:
        await super().on_event(event)
        await self.hold_callback()


@dataclass(frozen=True)
class HeldEventTurn:
    client: OmpRpcClient
    observer: StubbornEventObserver
    running: asyncio.Task[None]
    process: asyncio.subprocess.Process
    peer: psutil.Process


HeldEventFactory = Callable[[PeerScenario], AbstractAsyncContextManager[HeldEventTurn]]


async def echo_tool(call: OmpHostToolCall) -> OmpToolResult:
    return OmpToolResult(
        content=[OmpContent(type=OmpContentKind.TEXT, text=call.toolName)]
    )


async def cancel_ui(_request: OmpUIRequest) -> OmpUIResponse:
    return OmpUIResponse(cancelled=True)
