"""Owned asynchronous workers and gated telemetry for lifecycle probes."""

from __future__ import annotations

import asyncio
from typing import Any

from nooa.unifiedllm import FakeLLMClient

from band.adapters.nooa import NooaRoomAgent
from band.core.types import MessageType
from band.testing import FakeAgentTools
from band.testing.nooa import room_agent_class


class OwnedWorker:
    """A persistent background task with an explicit owner-supplied close method."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self.task = asyncio.create_task(self.run())

    async def run(self) -> None:
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.closed.set()

    @property
    def running(self) -> bool:
        return self.started.is_set() and not self.task.done()

    async def close(self) -> None:
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


class WorkerOwner:
    """Keeps external handles so a missing adapter teardown cannot leak test tasks."""

    def __init__(self) -> None:
        self.workers: list[OwnedWorker] = []
        self.agent_close_calls = 0

    def agent_class(self) -> type[NooaRoomAgent]:
        owner = self

        async def respond(agent: NooaRoomAgent, message: str) -> None:
            worker = OwnedWorker()
            agent.worker = worker
            owner.workers.append(worker)
            await worker.started.wait()
            await agent.room.band_no_reply()

        async def close(agent: NooaRoomAgent) -> None:
            owner.agent_close_calls += 1
            await agent.worker.close()

        return room_agent_class(
            FakeLLMClient(),
            namespace={"respond": respond, "close": close},
        )

    async def close(self) -> None:
        for worker in self.workers:
            await worker.close()


class BlockedThoughtTools(FakeAgentTools):
    """Fake tools with no execution-scope guard and an event-controlled thought send."""

    def __init__(self) -> None:
        super().__init__()
        self.posting = asyncio.Event()
        self.release = asyncio.Event()

    async def send_event(
        self,
        content: str,
        message_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if message_type == MessageType.THOUGHT:
            self.posting.set()
            await self.release.wait()
        return await super().send_event(content, message_type, metadata)
