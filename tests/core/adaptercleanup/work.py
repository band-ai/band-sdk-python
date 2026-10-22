"""A turn that keeps using its resource during cancellation finalization."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from band.agent import Agent
from band.client.streaming import ControlMode
from band.runtime.cycle import TurnScope
from band.runtime.execution import ExecutionContext
from band.runtime.oneshot import OneShotInvoker
from band.runtime.runtime import AgentRuntime
from tests.adaptercleanup import (
    RESOURCE_PATH,
    Host,
    HostKind,
    TeardownAdapter,
    host_for,
)
from tests.conftest import make_message_event
from tests.runtime.helpers import ROOM_ID
from tests.runtime.oneshot.kit import msg_body

WORK_ID = "work"


class WorkingAdapter(TeardownAdapter):
    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__()
        self.client = client
        self.entered = asyncio.Event()
        self.finalizing = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def on_message(self, *args: Any, **kwargs: Any) -> None:
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.finalizing.set()
            await self.release.wait()
            response = await self.client.get(RESOURCE_PATH)
            response.raise_for_status()
            self.finished.set()


class SuppressingWorkingAdapter(WorkingAdapter):
    async def on_message(self, *args: Any, **kwargs: Any) -> None:
        try:
            await super().on_message(*args, **kwargs)
        except asyncio.CancelledError:
            return


@dataclass
class AcceptedWork:
    host: Host
    adapter: WorkingAdapter
    client: httpx.AsyncClient
    operation: asyncio.Task[dict[str, Any]] | None = None
    turn: asyncio.Task[None] | None = None
    stops: list[asyncio.Task[None]] = field(default_factory=list)

    @property
    def runtime(self) -> AgentRuntime:
        assert isinstance(self.host.owner, Agent)
        return self.host.owner._runtime.runtime

    @property
    def execution(self) -> ExecutionContext:
        execution = self.runtime.executions[ROOM_ID]
        assert isinstance(execution, ExecutionContext)
        return execution

    async def start(self) -> None:
        await self.host.start()
        self.host.platform.add_message(WORK_ID)
        if isinstance(self.host.owner, OneShotInvoker):
            self.operation = asyncio.create_task(
                self.host.owner.handle_event(msg_body(msg_id=WORK_ID, room_id=ROOM_ID))
            )
        else:
            await self.runtime._on_room_joined(ROOM_ID, {})
            await self.runtime._on_room_event(
                ROOM_ID, make_message_event(room_id=ROOM_ID, msg_id=WORK_ID)
            )
        await self.adapter.entered.wait()
        if isinstance(self.host.owner, Agent):
            scope = self.execution.current_scope
            assert scope is not None and scope.handler is not None
            self.turn = scope.handler.task

    def start_stop(self) -> asyncio.Task[None]:
        stopping = asyncio.create_task(self.host.stop())
        self.stops.append(stopping)
        return stopping

    def cancel_oneshot(self) -> None:
        if self.operation is not None:
            self.operation.cancel()

    async def close(self) -> None:
        self.adapter.release.set()
        self.cancel_oneshot()
        if not self.stops:
            self.start_stop()
        tasks = self.stops if self.operation is None else [self.operation, *self.stops]
        if self.turn is not None:
            tasks = [self.turn, *tasks]
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.host.stop()


@asynccontextmanager
async def accepted_work(
    client: httpx.AsyncClient,
    kind: HostKind,
    adapter_type: type[WorkingAdapter] = WorkingAdapter,
) -> AsyncIterator[AcceptedWork]:
    adapter = adapter_type(client)
    adapter.add_cleanup(client.aclose)
    async with host_for(adapter, kind) as host:
        work = AcceptedWork(host, adapter, client)
        try:
            await work.start()
            yield work
        finally:
            await work.close()


@contextmanager
def observe_shutdown(scope: TurnScope) -> Iterator[asyncio.Event]:
    """Acknowledge the real termination call; the scope has no shutdown event."""
    entered = asyncio.Event()
    terminate = scope.terminate

    def observe(
        kind: ControlMode | None = None,
        *,
        cooperative: bool = False,
        observed: bool = False,
        shutdown: bool = False,
    ) -> None:
        terminate(kind, cooperative=cooperative, observed=observed, shutdown=shutdown)
        if shutdown:
            entered.set()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scope, "terminate", observe)
        yield entered
