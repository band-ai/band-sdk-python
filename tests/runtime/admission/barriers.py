"""A one-room platform peer whose requests can be held, refused or lost, and the
started room driven against it.

The room runs over the real generated REST client and ``BandLink``; only the
HTTP boundary is replaced. A scenario scripts what the peer answers to one call,
optionally holds that answer until the test has delivered a control, then reads
the outcome as the platform and the model saw it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

import httpx
from band_sdk_core import ClaimRegistry

from band.client.streaming import DeliveryStatus
from band.platform.event import MessageEvent
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.types import SessionConfig
from tests.conftest import make_message_event
from tests.runtime.batching.kit import WAIT_SECONDS, Turns
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import AGENT_ID, ROOM_ID, LifecyclePlatform, rest_client_over

REFUSAL = {
    "error": {
        "code": "refused",
        "message": "The platform refused the write",
        "request_id": "request-1",
    }
}


class Endpoint(StrEnum):
    """The requests a room makes that a scenario can script."""

    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    WORKING = "working"  # the working:true report that opens a turn
    CONTEXT = "context"  # the history read that hydrates a room


@dataclass(frozen=True)
class Call:
    """One scriptable request: an endpoint and, for a delivery write, its message."""

    endpoint: Endpoint
    message_id: str | None = None

    @classmethod
    def of(cls, request: httpx.Request) -> Call | None:
        """The scriptable call a request is, or ``None`` for any other request."""
        path = request.url.path.split("/")
        match (request.method, path[-1]):
            case ("POST", "processing" | "processed" | "failed" as tail):
                return cls(Endpoint(tail), path[-2])
            case ("POST", "activity") if b'"working":true' in request.content.replace(
                b" ", b""
            ):
                return cls(Endpoint.WORKING)
            case ("GET", "context"):
                return cls(Endpoint.CONTEXT)
        return None

    def __str__(self) -> str:
        return " ".join(filter(None, (self.endpoint, self.message_id)))


def writes(endpoint: Endpoint, message_ids: Iterable[str]) -> list[str]:
    """The timeline entries of one ``endpoint`` write per message, in order."""
    return [str(Call(endpoint, message_id)) for message_id in message_ids]


def run(message_ids: Iterable[str]) -> str:
    """The timeline entry of one model invocation over ``message_ids``."""
    return f"run {' '.join(message_ids)}"


class Answer(StrEnum):
    """What the peer decides when a scripted call reaches it."""

    ACCEPT = "accept"
    REFUSE = "refuse"  # an ordinary refusal: not a stop, not retryable
    STOP = "stop"  # the room's execution is stopped: HTTP 204, or working:false echoed
    LOST = "lost"  # accepted and applied, but the client gives up waiting

    @property
    def applied(self) -> bool:
        """Whether the platform records the write."""
        return self in {Answer.ACCEPT, Answer.LOST}

    def recorded(self, case: Case) -> tuple[str, ...]:
        """The originals in processing once the case's target write is answered so."""
        return (*case.before, case.target) if self.applied else case.before


@dataclass
class Hold:
    """A decided answer the peer has not yet delivered.

    The peer applies its decision on arrival; releasing only delivers (or, for
    ``Answer.LOST``, abandons) the response.
    """

    answer: Answer
    suppress_cancel: bool = False
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)

    def release(self) -> None:
        self.released.set()

    async def wait(self) -> None:
        """Wait for the release; a transport that resists cancellation keeps waiting."""
        while not self.released.is_set():
            try:
                await self.released.wait()
            except asyncio.CancelledError:
                if not self.suppress_cancel:
                    raise


class Platform:
    """The platform side of one room, behind the real REST client."""

    def __init__(self, *, journal: list[str]) -> None:
        self.peer = LifecyclePlatform()
        # One timeline shared with the model: every scriptable request and every
        # model invocation, in the order they happened.
        self.journal = journal
        self.calls: list[Call] = []
        self.posted: list[str] = []
        self.scripts: dict[Call, Answer] = {}
        self.holds: dict[Call, Hold] = {}

    def post(self, *message_ids: str) -> list[MessageEvent]:
        """Store the messages and return the WebSocket event for each."""
        events: list[MessageEvent] = []
        for message_id in message_ids:
            self.peer.add_message(message_id)
            row = self.peer.messages[-1]
            self.posted.append(message_id)
            events.append(
                make_message_event(
                    room_id=ROOM_ID,
                    msg_id=message_id,
                    sender_id=row["sender_id"],
                    inserted_at=row["inserted_at"],
                )
            )
        return events

    def script(self, call: Call, answer: Answer) -> None:
        """Answer every ``call`` this way until it is scripted again."""
        self.scripts[call] = answer

    def hold(
        self,
        call: Call,
        answer: Answer = Answer.ACCEPT,
        *,
        suppress_cancel: bool = False,
    ) -> Hold:
        """Decide the next ``call`` as ``answer`` but deliver nothing until released."""
        self.holds[call] = hold = Hold(answer, suppress_cancel=suppress_cancel)
        return hold

    def stop(self) -> None:
        self.peer.stopped = True

    def play(self) -> None:
        self.peer.stopped = False

    @property
    def statuses(self) -> dict[str, str | None]:
        """Each posted message's delivery state as the platform recorded it."""
        marks = dict(self.peer.accepted_marks)
        return {message_id: marks.get(message_id) for message_id in self.posted}

    def writes_to(self, *endpoints: Endpoint) -> list[str]:
        """Every request that reached the platform on these endpoints, in order."""
        return [str(call) for call in self.calls if call.endpoint in endpoints]

    async def answer(self, request: httpx.Request) -> httpx.Response:
        call = Call.of(request)
        if call is None:
            return self.peer.answer(request)
        self.calls.append(call)
        self.journal.append(str(call))
        held = self.holds.pop(call, None)
        answer = held.answer if held else self.scripts.get(call, Answer.ACCEPT)
        response = self.decide(request, answer)
        if held is not None:
            held.entered.set()
            await held.wait()
        if answer is Answer.LOST:
            raise httpx.ReadTimeout("the response never arrived", request=request)
        return response

    def decide(self, request: httpx.Request, answer: Answer) -> httpx.Response:
        match answer:
            case Answer.ACCEPT | Answer.LOST:
                return self.peer.answer(request)
            case Answer.REFUSE:
                return httpx.Response(422, json=REFUSAL)
            case Answer.STOP if request.url.path.endswith("/activity"):
                return httpx.Response(200, json={"data": {"working": False}})
            case Answer.STOP:
                return httpx.Response(204)


@dataclass
class Model(Turns):
    """The provider: records each invocation on the shared timeline."""

    journal: list[str] = field(default_factory=list)
    entered: asyncio.Event = field(default_factory=asyncio.Event)

    async def record(self, ids: tuple[str, ...]) -> None:
        self.journal.append(run(ids))
        self.entered.set()
        await super().record(ids)


@dataclass(frozen=True)
class Case:
    """The originals a scenario queues and how the room consumes them.

    ``at`` is the original whose write the scenario hits.
    """

    name: str
    batching: bool
    size: int = 1
    at: int = 0

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(f"m{number}" for number in range(1, self.size + 1))

    @property
    def target(self) -> str:
        return self.ids[self.at]

    @property
    def before(self) -> tuple[str, ...]:
        """The originals admitted ahead of the target."""
        return self.ids[: self.at]

    def __str__(self) -> str:
        return self.name


OFF = Case("off-singleton", batching=False)
ON = Case("on-singleton", batching=True)
BATCH = Case("on-batch", batching=True, size=3)
FIRST, MIDDLE, LAST = (
    replace(BATCH, name=f"batch-{where}", at=at)
    for at, where in enumerate(("first", "middle", "last"))
)
SHAPES = (OFF, ON, BATCH)
POSITIONS = (OFF, ON, FIRST, MIDDLE, LAST)


@dataclass(frozen=True)
class Window:
    """An await between a turn's full admission and the model's invocation.

    ``call`` is the request the room is waiting on there and ``settings`` make
    the room issue it; a ``warm`` room has hydrated before, so the request is
    not part of admission.
    """

    name: str
    call: Call
    settings: dict[str, Any]
    warm: bool = False

    def __str__(self) -> str:
        return self.name


REFRESH = Window(
    "context-refresh",
    Call(Endpoint.CONTEXT),
    {"enable_context_hydration": True, "context_cache_ttl_seconds": 0},
    warm=True,
)
REPORT = Window(
    "activity-report", Call(Endpoint.WORKING), {"enable_working_state": True}
)
WINDOWS = (REFRESH, REPORT)


def processed(ids: Iterable[str]) -> dict[str, str | None]:
    """The delivery states of originals the platform has settled."""
    return dict.fromkeys(ids, DeliveryStatus.PROCESSED)


@dataclass(frozen=True)
class Observed:
    """What the platform and the model saw, as a scenario's outcome."""

    statuses: dict[str, str | None]
    runs: list[tuple[str, ...]]
    terminal_writes: list[str]

    @classmethod
    def recoverable(
        cls,
        ids: tuple[str, ...],
        *,
        processing: Iterable[str] = (),
        runs: list[tuple[str, ...]] | None = None,
    ) -> Observed:
        """Nothing is settled: ``processing`` originals stay in processing and every
        other original is untouched, ready for recovery to select again."""
        marked = set(processing)
        return cls(
            statuses={
                message_id: DeliveryStatus.PROCESSING if message_id in marked else None
                for message_id in ids
            },
            runs=runs or [],
            terminal_writes=[],
        )

    @classmethod
    def settled(
        cls, ids: tuple[str, ...], *, runs: list[tuple[str, ...]] | None = None
    ) -> Observed:
        """Every original processed by one write each, after the given model runs."""
        return cls(
            statuses=processed(ids),
            runs=[ids] if runs is None else runs,
            terminal_writes=writes(Endpoint.PROCESSED, ids),
        )


@dataclass
class Room:
    case: Case
    platform: Platform
    model: Model
    ctx: ExecutionContext
    registry: ClaimRegistry
    link: BandLink
    settings: dict[str, Any]

    async def arrive(self) -> None:
        """Post the case's originals and deliver them over the socket together."""
        for event in self.platform.post(*self.case.ids):
            await self.ctx.on_event(event)

    async def until(self, condition: Callable[[], bool]) -> None:
        await wait_for_condition(condition, timeout=WAIT_SECONDS)

    async def called(self, call: Call) -> None:
        """Wait until the platform has received ``call``."""
        await self.until(lambda: call in self.platform.calls)

    async def reached(self, hold: Hold) -> None:
        """Wait until the held request is at the platform, undelivered."""
        await self.until(hold.entered.is_set)

    async def quiet(self) -> None:
        """Wait for the room to finish the attempt it is in."""
        await self.until(lambda: not self.ctx.is_processing)

    async def recover(self) -> None:
        """Ask the room to read the platform again, as a periodic pass would."""
        await self.ctx.request_resync()

    async def settled(self) -> None:
        """Wait until the platform has every original processed."""
        await self.until(lambda: self.platform.statuses == processed(self.case.ids))

    def interrupt(self) -> None:
        self.ctx.interrupt()

    def stop(self) -> None:
        """A native STOP: the platform stops the room and the control reaches it."""
        self.platform.stop()
        self.ctx.stop_room()

    async def play(self) -> None:
        self.platform.play()
        await self.ctx.resume_room()

    async def restart(self) -> None:
        """Replace the room's execution, keeping the runtime's claim registry."""
        await self.ctx.stop()
        self.ctx = build_context(
            self.case, self.link, self.model, self.registry, self.settings
        )
        await self.ctx.start()

    def observed(self) -> Observed:
        return Observed(
            statuses=self.platform.statuses,
            runs=list(self.model.ids),
            terminal_writes=self.platform.writes_to(
                Endpoint.PROCESSED, Endpoint.FAILED
            ),
        )


class Stop(StrEnum):
    """How a stop reaches a room."""

    NATIVE = "native-stop"  # the platform stops the room and the control arrives
    PLATFORM = "platform-stop"  # only the platform's refusals tell the room
    SHUTDOWN = "shutdown"  # the host cancels the room's task

    @property
    def answer(self) -> Answer:
        """How the platform answers the call a stop interrupts.

        A native stop finds the answer already on its way, so it arrives late
        and successful; otherwise the platform refuses or the host cancels
        before anything lands.
        """
        return Answer.ACCEPT if self is Stop.NATIVE else Answer.STOP

    async def deliver(self, room: Room) -> None:
        match self:
            case Stop.NATIVE:
                room.stop()
            case Stop.PLATFORM:
                room.platform.stop()
            case Stop.SHUTDOWN:
                await room.ctx.stop()

    async def resume(self, room: Room) -> None:
        """What lets the stopped room's originals run again."""
        match self:
            case Stop.NATIVE:
                await room.play()
            case Stop.PLATFORM:
                room.platform.play()
                await room.recover()
            case Stop.SHUTDOWN:
                await room.restart()


def build_context(
    case: Case,
    link: BandLink,
    model: Model,
    registry: ClaimRegistry,
    settings: dict[str, Any],
) -> ExecutionContext:
    return ExecutionContext(
        ROOM_ID,
        link,
        model.single,
        config=SessionConfig(consume_all_queued=case.batching, **settings),
        agent_id=AGENT_ID,
        on_execute_batch=model.batch if case.batching else None,
        claim_registry=registry,
    )


@contextlib.asynccontextmanager
async def started_room(
    platform: Platform,
    model: Model,
    case: Case,
    *,
    warm: bool = False,
    **settings: Any,
) -> AsyncIterator[Room]:
    """A started room over ``platform``; ``settings`` adjust its ``SessionConfig``.

    A ``warm`` room has already hydrated its context, as one that served before.
    """
    settings = {
        "enable_context_hydration": False,
        "enable_working_state": False,
    } | settings
    registry = ClaimRegistry()
    async with rest_client_over(platform.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        ctx = build_context(case, link, model, registry, settings)
        if warm:
            await ctx.hydrate()
        await ctx.start()
        room = Room(case, platform, model, ctx, registry, link, settings)
        try:
            yield room
        finally:
            model.release.set()
            await room.ctx.stop()
