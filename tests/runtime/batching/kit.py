"""Turn and control host doubles shared by the room execution kits."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from band.client.streaming import MessageCreatedPayload
from band.core.exceptions import InputNotAccepted
from band.platform.event import MessageEvent
from band.runtime.execution import ExecutionContext


@dataclass
class Turns:
    """What the host was asked to run, one entry per invocation.

    A non-message event is recorded by its type; ``fail_on`` makes any turn
    that includes one of those messages raise after it is recorded.
    """

    ids: list[tuple[str, ...]] = field(default_factory=list)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    gate: bool = False
    fail_on: set[str] = field(default_factory=set)
    # While False the host refuses ordinary input, as a busy adapter does; each
    # refused offer is kept.
    accepting: bool = True
    refused: list[tuple[str, ...]] = field(default_factory=list)
    # The payload of every message the host was offered, as the room delivered it.
    offered: list[MessageCreatedPayload] = field(default_factory=list)

    @property
    def order(self) -> list[str]:
        """Every input the host was given, in the order it saw them."""
        return [item for turn in self.ids for item in turn]

    async def single(self, ctx: ExecutionContext, event: Any) -> None:
        if isinstance(event, MessageEvent):
            self.offered.append(event.payload)
        await self.record(
            (event.payload.id if isinstance(event, MessageEvent) else event.type,)
        )

    async def batch(self, ctx: ExecutionContext, events: tuple[Any, ...]) -> None:
        self.offered.extend(event.payload for event in events)
        await self.record(tuple(event.payload.id for event in events))

    async def record(self, ids: tuple[str, ...]) -> None:
        if not self.accepting:
            self.refused.append(ids)
            raise InputNotAccepted("busy")
        self.ids.append(ids)
        if self.gate:
            await self.release.wait()
        if self.fail_on.intersection(ids):
            raise RuntimeError("the model failed")


@dataclass
class ControlScript:
    """A host's control vocabulary: what it recognizes and what it consumes.

    ``recognized`` is read live, so a handler can change what the next
    candidate classifies as. ``declines`` are recognized messages the handler
    then refuses to consume (an expired request), ``raises`` ones whose handler
    fails and ``hangs`` ones whose handler runs until it is cancelled.
    """

    recognized: set[str] = field(default_factory=set)
    declines: set[str] = field(default_factory=set)
    raises: set[str] = field(default_factory=set)
    hangs: set[str] = field(default_factory=set)
    # Recognized messages whose handler began, consumed or not.
    started: list[str] = field(default_factory=list)
    handled: list[str] = field(default_factory=list)
    on_handled: Callable[[str], None] = lambda message_id: None

    def recognizes(self, ctx: ExecutionContext, event: MessageEvent) -> bool:
        return event.payload is not None and event.payload.id in self.recognized

    async def handle(self, ctx: ExecutionContext, event: MessageEvent) -> bool:
        message_id = _payload_id(event)
        if message_id in self.declines or message_id not in self.recognized:
            return False
        self.started.append(message_id)
        if message_id in self.hangs:
            await asyncio.Event().wait()
        if message_id in self.raises:
            raise RuntimeError("the control handler failed")
        self.handled.append(message_id)
        self.on_handled(message_id)
        return True


def _payload_id(event: Any) -> str:
    return event.payload.id
