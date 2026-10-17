"""A real request executor with an observable cancellation barrier."""

from __future__ import annotations

import asyncio

from a2a.helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue


class HeldExecutor(AgentExecutor):
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.finalizing = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message is not None
        await event_queue.enqueue_event(new_task_from_user_message(context.message))
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.finalizing.set()
            await self.release.wait()
            self.finished.set()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError
