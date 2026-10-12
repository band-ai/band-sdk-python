"""Shared doubles and room-lifecycle steps for AgentRuntime tests."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from band.runtime.runtime import AgentRuntime


async def settled(runtime: AgentRuntime, room_id: str) -> None:
    """Let the room's owned lifecycle task finish, whatever its outcome."""
    record = runtime.rooms.record(room_id)
    if record is not None and record.task is not None:
        await asyncio.wait({record.task})


async def joined(runtime: AgentRuntime, room_id: str) -> None:
    """Join as presence does, then let the requested admission settle."""
    await runtime._on_room_joined(room_id, {})
    await settled(runtime, room_id)


async def left(runtime: AgentRuntime, room_id: str) -> None:
    """Leave as presence does, then let the requested cleanup settle."""
    await runtime._on_room_left(room_id)
    await settled(runtime, room_id)


async def until_admitted(runtime: AgentRuntime, room_id: str) -> None:
    while room_id not in runtime.executions:
        await settled(runtime, room_id)
        await asyncio.sleep(0)


class HeldExecution:
    """A custom execution whose start can be held or failed."""

    def __init__(self, room_id: str, gate: StartGate) -> None:
        self.room_id = room_id
        self.gate = gate
        self.started = False
        self.stopped = False
        self.resyncs = 0
        self.events: list[object] = []

    async def start(self) -> None:
        self.gate.entered.set()
        await self.gate.release.wait()
        if self.gate.failures:
            self.gate.failures -= 1
            raise RuntimeError("execution start failed")
        self.started = True

    async def stop(self, timeout: float | None = None) -> bool:
        self.stopped = True
        return self.gate.graceful

    async def on_event(self, event: object) -> None:
        self.events.append(event)

    async def request_resync(self) -> None:
        self.resyncs += 1


class StartGate:
    """Builds ``HeldExecution``s; each start waits for ``release``."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.failures = 0
        self.graceful = True
        self.built: list[HeldExecution] = []

    def hold(self) -> None:
        self.entered.clear()
        self.release.clear()

    def __call__(self, room_id: str, link, *, hub_room_id=None) -> HeldExecution:
        execution = HeldExecution(room_id, self)
        self.built.append(execution)
        return execution


class QueueLink:
    """Feeds platform events to the real presence consumer."""

    def __init__(self, link: MagicMock) -> None:
        self.events: asyncio.Queue[object] = asyncio.Queue()

        async def stream():
            while True:
                yield await self.events.get()

        link.__aiter__ = lambda _self: stream()
