"""A native command's identity and actual terminal synchronization."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from band.core.protocols import AgentToolsProtocol
from band.core.turn_lifecycle import TurnTask


@dataclass(eq=False)
class CommandResource:
    tools: AgentToolsProtocol
    generation: int
    thread_id: str
    turn_id: str
    item_id: str
    process_id: str | None
    token: object = field(default_factory=object)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    terminal: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    owner: TurnTask | None = None

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.thread_id, self.turn_id, self.item_id
