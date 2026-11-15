"""Handler doubles for failure reporting and hydrated turn observations."""

from __future__ import annotations

import asyncio

from band.platform.event import MessageEvent, PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.messages import PlatformMessage
from tests.runtime.executioninterrupt.kit import backlog_message
from tests.runtime.helpers import LifecyclePlatform


def websocket_source(peer: LifecyclePlatform, message_id: str) -> MessageEvent:
    """Deliver a stored original through its WebSocket event."""
    return peer.event(message_id)


def backlog_source(peer: LifecyclePlatform, message_id: str) -> PlatformMessage:
    """Deliver an original through the backlog message representation."""
    return backlog_message(message_id)


class FinalAttemptFailure:
    """Fail every attempt, holding the final one before it can report."""

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.attempts = 0
        self.final_started = asyncio.Event()
        self.finish_final = asyncio.Event()

    async def __call__(self, ctx: ExecutionContext, event: PlatformEvent) -> None:
        self.attempts += 1
        if self.attempts == self.budget:
            self.final_started.set()
            await self.finish_final.wait()
        raise RuntimeError("provider down")


class HydratedTurns:
    """Record the history each delivered original actually sees."""

    def __init__(self) -> None:
        self.observed: list[tuple[str, list[str]]] = []

    async def __call__(self, ctx: ExecutionContext, event: PlatformEvent) -> None:
        assert isinstance(event, MessageEvent)
        assert event.payload is not None
        history = [message["id"] for message in ctx.build_context().messages]
        self.observed.append((event.payload.id, history))
