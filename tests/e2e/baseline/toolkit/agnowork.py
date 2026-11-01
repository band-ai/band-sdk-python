"""Real synchronous native Agno work with observable worker-body completion."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx

from band.core.simple_adapter import SimpleAdapter
from band.integrations.agno.activity import NATIVE_BODY_LIFECYCLE, NativeBodyStatus
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.activitywork import Operation
from tests.e2e.baseline.toolkit.logs import sdk_logs_at

WORKER_BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=9)


@dataclass(frozen=True)
class NativeBodyRecord:
    sequence: int
    id: str
    room: str
    name: str
    status: NativeBodyStatus


class NativeBodies(logging.Handler):
    """Join actual resource settlement by its immutable body identity."""

    def __init__(self, *, deadline_s: float) -> None:
        super().__init__(logging.INFO)
        self.deadline_s = deadline_s
        self.records: list[NativeBodyRecord] = []
        self._loop = asyncio.get_running_loop()
        self._changed = asyncio.Event()

    def cursor(self) -> int:
        return len(self.records)

    def emit(self, record: logging.LogRecord) -> None:
        if isinstance(value := getattr(record, NATIVE_BODY_LIFECYCLE, None), dict):
            self._loop.call_soon_threadsafe(self._record, dict(value))

    def _record(self, value: dict[str, Any]) -> None:
        self.records.append(NativeBodyRecord(sequence=len(self.records) + 1, **value))
        self._changed.set()

    @contextmanager
    def observing(self) -> Iterator[None]:
        logger = logging.getLogger("band.integrations.agno.activity")
        with sdk_logs_at(logger.name, logging.INFO):
            logger.addHandler(self)
            try:
                yield
            finally:
                logger.removeHandler(self)

    async def _wait(
        self, select: Callable[[NativeBodyRecord], bool], *, since: int, phase: str
    ) -> NativeBodyRecord:
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
            raise TimeoutError(f"Missing native body barrier: {phase}") from None

    async def admitted(self, room: str, name: str, *, since: int) -> NativeBodyRecord:
        return await self._wait(
            lambda record: (
                record.room == room
                and record.name == name
                and record.status == NativeBodyStatus.ADMITTED
            ),
            since=since,
            phase=f"admitted {name} in room {room}",
        )

    async def settled(self, admitted: NativeBodyRecord) -> NativeBodyRecord:
        return await self._wait(
            lambda record: (
                (record.id, record.room, record.name)
                == (admitted.id, admitted.room, admitted.name)
                and record.status == NativeBodyStatus.SETTLED
            ),
            since=admitted.sequence,
            phase=f"settled resource {admitted.id}",
        )


@dataclass
class WorkerInvocation:
    started: threading.Event = field(default_factory=threading.Event)
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: threading.Event = field(default_factory=threading.Event)
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    result: str = ""


class AgnoWorkerWork:
    """Keep external resources open until their actual synchronous users settle."""

    def __init__(self, client: httpx.Client, base_url: str) -> None:
        self._client = client
        self._base_url = base_url
        self._loop = asyncio.get_running_loop()
        self._admission = threading.Lock()
        self.note = uuid4().hex
        self.operations = {
            key: WorkerInvocation() for key in (Operation.A, Operation.B)
        }

    def __getitem__(self, key: Operation) -> WorkerInvocation:
        return self.operations[key]

    @property
    def name(self) -> str:
        return self.read_worker_result.__name__

    def read_worker_result(self, key: Operation, private_note: str) -> str:
        """Read the requested operation's fresh opaque result from its real resource."""
        if private_note != self.note:
            raise ValueError("The operation requires its current private note")
        work = self.operations[Operation(key)]
        with self._admission:
            if work.started.is_set():
                raise ValueError("This operation already has an invocation")
            work.started.set()
        try:
            response = self._client.get(f"{self._base_url}/users/1")
            response.raise_for_status()
            work.result = f"{response.json()['name']}-{uuid4().hex}"
            self._loop.call_soon_threadsafe(work.entered.set)
            work.release.wait()
            return work.result
        finally:
            self._loop.call_soon_threadsafe(work.settled.set)

    def adapter(self, settings: BaselineSettings) -> SimpleAdapter[Any]:
        from agno.agent import Agent as AgnoAgent  # noqa: PLC0415 -- optional adapter dependency
        from agno.models.anthropic import Claude  # noqa: PLC0415 -- optional adapter dependency

        from band.adapters.agno import AgnoAdapter  # noqa: PLC0415 -- optional adapter dependency

        return AgnoAdapter(
            agent=AgnoAgent(
                model=Claude(id=settings.llm_models.anthropic_model),
                instructions=(
                    f"Call {self.name} afresh with the requested key and private_note. "
                    "Its current opaque value is available only from this call, never history. "
                    "Report its complete result using band_send_message."
                ),
                tools=[self.read_worker_result],
            ),
            emit=(),
        )

    def instruction(self, key: Operation) -> str:
        return (
            f"Call {self.name} now with key={key.value} "
            f"and private_note={self.note}. Report only its fresh result to this room."
        )

    async def entered(self, key: Operation) -> None:
        async with asyncio.timeout(WORKER_BUDGET.deadline_s):
            await self[key].entered.wait()

    async def settled(self, key: Operation) -> None:
        async with asyncio.timeout(WORKER_BUDGET.deadline_s):
            await self[key].settled.wait()

    @asynccontextmanager
    async def releasing_on_exit(self) -> AsyncIterator[None]:
        try:
            yield
        finally:
            for work in self.operations.values():
                work.release.set()
            for key, work in self.operations.items():
                if work.started.is_set():
                    await self.settled(key)
