"""Join actual native bodies through one run-owned loop/worker bridge."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from enum import Enum, StrEnum, auto
from functools import partial
from uuid import uuid4

from band.core.exceptions import CleanupIncompleteError
from band.core.protocols import AgentToolsProtocol
from band.core.turn_lifecycle import (
    resource_tools,
    TaskRole,
    TurnTask,
    bind_turn_task,
    terminal_deadline,
)
from band.integrations.agno.context import current_activity, current_tools
from band.runtime.activity import activity_observer_of
from band.runtime.toolactivity import (
    end_activity,
    require_activity_authority,
    start_native_tool_activity,
    suppress_tool_activity,
)

logger = logging.getLogger(__name__)
NATIVE_BODY_LIFECYCLE = "band_agno_body"


class NativeBodyStatus(StrEnum):
    ADMITTED = "admitted"
    SETTLED = "settled"


class Observation(Enum):
    BEGIN = auto()
    END = auto()
    CLOSE = auto()


@dataclass(eq=False)
class Body:
    name: str
    tools: AgentToolsProtocol
    token: str = field(default_factory=lambda: uuid4().hex)
    terminal: asyncio.Event = field(default_factory=asyncio.Event)
    revoked: threading.Event = field(default_factory=threading.Event)
    owner: TurnTask | None = None

    @contextmanager
    def executing(self, *, closing: bool = False) -> Iterator[None]:
        if not closing and self.revoked.is_set():
            raise asyncio.CancelledError
        token = current_tools.set(self.tools)
        try:
            with suppress_tool_activity():
                yield
        finally:
            current_tools.reset(token)


class RunActivity:
    """Keep native terminal delivery available after an awaiting run is cancelled."""

    def __init__(self, tools: AgentToolsProtocol) -> None:
        self.tools = tools
        self._loop = asyncio.get_running_loop()
        self._thread = threading.get_ident()
        self._accepting = True
        self._bodies: set[Body] = set()
        self._queue: asyncio.Queue[tuple[Observation, Body | None]] = asyncio.Queue()
        self._consumer = asyncio.create_task(
            self._consume(), name="agno-native-activity"
        )

    @contextmanager
    def bound(self) -> Iterator[None]:
        token = current_activity.set(self)
        try:
            yield
        finally:
            current_activity.reset(token)

    def on_closed(self, callback: Callable[[], None]) -> None:
        self._consumer.add_done_callback(lambda _: callback())

    def begin(self, name: str) -> Body:
        if threading.get_ident() == self._thread:
            return self._admit(name)
        ready: concurrent.futures.Future[Body] = concurrent.futures.Future()
        self._loop.call_soon_threadsafe(self._begin_on_loop, name, ready)
        return ready.result()

    def _begin_on_loop(self, name: str, ready: concurrent.futures.Future[Body]) -> None:
        try:
            ready.set_result(self._admit(name))
        except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001 -- forward admission failure to the waiting worker
            ready.set_exception(error)

    def _admit(self, name: str) -> Body:
        if not self._accepting:
            raise asyncio.CancelledError
        require_activity_authority(activity_observer_of(self.tools))
        body = Body(name, self.tools)
        worker = asyncio.create_task(body.terminal.wait())
        body.owner = bind_turn_task(
            self.tools, worker, role=TaskRole.RESOURCE, request_stop=body.revoked.set
        )
        worker.add_done_callback(partial(self._settled, body))
        body.tools = resource_tools(self.tools, body.owner)
        self._bodies.add(body)
        self._queue.put_nowait((Observation.BEGIN, body))
        return body

    def _settled(self, body: Body, task: asyncio.Task[bool]) -> None:
        task.result()
        self._diagnose(body, NativeBodyStatus.SETTLED)

    def _diagnose(self, body: Body, status: NativeBodyStatus) -> None:
        logger.info(
            "Agno native body lifecycle",
            extra={
                NATIVE_BODY_LIFECYCLE: {
                    "id": body.token,
                    "room": getattr(self.tools, "room_id", None),
                    "name": body.name,
                    "status": status,
                }
            },
        )

    def end(self, body: Body) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (Observation.END, body))

    async def _consume(self) -> None:
        while True:
            observation, body = await self._queue.get()
            match observation:
                case Observation.BEGIN if body is not None:
                    if not body.revoked.is_set():
                        await start_native_tool_activity(
                            body.tools, body.token, body.name
                        )
                    self._diagnose(body, NativeBodyStatus.ADMITTED)
                case Observation.END if body is not None:
                    await end_activity(body.tools, body.token)
                    body.terminal.set()
                    self._bodies.discard(body)
            if not self._accepting and not self._bodies:
                return

    async def close(self, *, interrupted: bool = False) -> None:
        self._accepting = False
        self._queue.put_nowait((Observation.CLOSE, None))
        if interrupted:
            deadline = terminal_deadline()
            for body in tuple(self._bodies):
                if body.owner is not None:
                    body.owner.terminate(deadline=deadline)
            await asyncio.wait(
                {self._consumer}, timeout=max(0, deadline - self._loop.time())
            )
            if not self._consumer.done():
                raise CleanupIncompleteError("Agno native body is still running")
        await asyncio.shield(self._consumer)


@asynccontextmanager
async def observing_run(tools: AgentToolsProtocol) -> AsyncIterator[RunActivity]:
    run = RunActivity(tools)
    try:
        with run.bound():
            yield run
    except BaseException:
        await run.close(interrupted=True)
        raise
    else:
        await run.close()
