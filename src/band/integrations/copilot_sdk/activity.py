"""Ordered, session-owned pairing for Copilot's compaction notifications."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from copilot.generated.session_events import (
    SessionCompactionCompleteData,
    SessionCompactionStartData,
)

from band.core.protocols import AgentToolsProtocol
from band.runtime.toolactivity import end_activity, start_memory_activity


class CompactionSignal(Enum):
    START = auto()
    END = auto()
    RETIRE = auto()
    CLOSE = auto()


@dataclass(frozen=True)
class Observation:
    signal: CompactionSignal
    tools: AgentToolsProtocol | None


class SessionActivity:
    """No-ID overlaps remain unowned until every outstanding interval settles."""

    def __init__(self, session: Any) -> None:
        self.session = session
        self._tools: AgentToolsProtocol | None = None
        self._queue: asyncio.Queue[Observation] = asyncio.Queue()
        self._pending = 0
        self._current: tuple[AgentToolsProtocol, object] | None = None
        self._unsubscribe = session.on(self._observe)
        self._worker = asyncio.create_task(
            self._consume(), name="copilot-session-activity"
        )

    def bind(self, tools: AgentToolsProtocol) -> None:
        self._tools = tools

    def retire(self, tools: AgentToolsProtocol) -> None:
        if self._tools is tools:
            self._tools = None
        self._queue.put_nowait(Observation(CompactionSignal.RETIRE, tools))

    def _observe(self, event: Any) -> None:
        match event.data:
            case SessionCompactionStartData():
                self._queue.put_nowait(Observation(CompactionSignal.START, self._tools))
            case SessionCompactionCompleteData():
                self._queue.put_nowait(Observation(CompactionSignal.END, self._tools))

    async def _clear(self) -> None:
        if self._current is not None:
            tools, token = self._current
            self._current = None
            await end_activity(tools, token)

    async def _consume(self) -> None:
        while True:
            observation = await self._queue.get()
            match observation.signal:
                case CompactionSignal.START:
                    self._pending += 1
                    if self._pending == 1 and observation.tools is not None:
                        token = object()
                        self._current = (observation.tools, token)
                        await start_memory_activity(observation.tools, token)
                    else:
                        await self._clear()
                case CompactionSignal.END:
                    if self._pending:
                        self._pending -= 1
                    if not self._pending:
                        await self._clear()
                case CompactionSignal.RETIRE:
                    if (
                        self._current is not None
                        and self._current[0] is observation.tools
                    ):
                        await self._clear()
                case CompactionSignal.CLOSE:
                    await self._clear()
                    return

    async def close(self) -> None:
        self._tools = None
        if (unsubscribe := self._unsubscribe) is not None:
            self._unsubscribe = None
            unsubscribe()
            self._queue.put_nowait(Observation(CompactionSignal.CLOSE, None))
        await asyncio.shield(self._worker)
