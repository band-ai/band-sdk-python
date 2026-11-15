"""Event-driven capture of payload-free native task lifecycle diagnostics."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from tests.e2e.baseline.toolkit.logs import sdk_logs_at


@dataclass(frozen=True)
class NativeTaskRecord:
    sequence: int
    room: str
    generation: int
    task_id: str
    tool_use_id: str
    run_id: str | None
    revision: int
    status: str

    def same_execution(self, other: NativeTaskRecord) -> bool:
        return (
            self.room,
            self.generation,
            self.task_id,
            self.tool_use_id,
            self.run_id,
            self.revision,
        ) == (
            other.room,
            other.generation,
            other.task_id,
            other.tool_use_id,
            other.run_id,
            other.revision,
        )


class NativeTasks(logging.Handler):
    def __init__(
        self, *, task_field: str, terminal_statuses: frozenset[str], deadline_s: float
    ) -> None:
        super().__init__(logging.INFO)
        self.task_field = task_field
        self.terminal_statuses = terminal_statuses
        self.deadline_s = deadline_s
        self.records: list[NativeTaskRecord] = []
        self._loop = asyncio.get_running_loop()
        self._changed = asyncio.Event()

    def cursor(self) -> int:
        return len(self.records)

    def emit(self, record: logging.LogRecord) -> None:
        if isinstance(value := getattr(record, self.task_field, None), dict):
            self._loop.call_soon_threadsafe(self._record, dict(value))

    def _record(self, value: dict[str, Any]) -> None:
        self.records.append(NativeTaskRecord(sequence=len(self.records) + 1, **value))
        self._changed.set()

    @contextmanager
    def observing(self, package: ModuleType | str) -> Iterator[NativeTasks]:
        logger = logging.getLogger(
            package if isinstance(package, str) else package.__name__
        )
        with sdk_logs_at(package, logging.INFO):
            logger.addHandler(self)
            try:
                yield self
            finally:
                logger.removeHandler(self)

    async def _wait(
        self, select: Callable[[NativeTaskRecord], bool], *, since: int, phase: str
    ) -> NativeTaskRecord:
        try:
            async with asyncio.timeout(self.deadline_s):
                while True:
                    self._changed.clear()
                    if found := next(
                        (
                            record
                            for record in self.records
                            if record.sequence > since and select(record)
                        ),
                        None,
                    ):
                        return found
                    await self._changed.wait()
        except TimeoutError:
            raise TimeoutError(f"Missing native task barrier: {phase}") from None

    async def started(self, room: str, *, since: int) -> NativeTaskRecord:
        return await self._wait(
            lambda record: (
                record.room == room and record.status not in self.terminal_statuses
            ),
            since=since,
            phase=f"admitted background task in room {room}",
        )

    async def terminal(self, started: NativeTaskRecord) -> NativeTaskRecord:
        return await self._wait(
            lambda record: (
                record.same_execution(started)
                and record.status in self.terminal_statuses
            ),
            since=started.sequence,
            phase=f"terminal for native task {started.task_id}",
        )
