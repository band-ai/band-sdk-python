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
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

import httpx
from band_sdk_core import ClaimRegistry

from band.client.streaming import AgentControlPayload, ControlMode, DeliveryStatus
from band.platform.event import MessageEvent
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event
from tests.runtime.batching.kit import ControlScript, Turns
from tests.runtime.conftest import Waiting
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    LifecyclePlatform,
    rest_client_over,
)

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
    NEXT = "next"  # the /next read a singleton recovery pass polls
    PAGE = "page"  # the actionable-list read a batching recovery pass selects from


# Recovery polls are scriptable but are not part of a turn's timeline.
POLLS = frozenset({Endpoint.NEXT, Endpoint.PAGE})


@dataclass(frozen=True)
class Call:
    """One scriptable request: an endpoint and, for a delivery write, its message."""

    endpoint: Endpoint
    message_id: str | None = None

    @classmethod
    def processing(cls, message_id: str) -> Call:
        return cls(endpoint=Endpoint.PROCESSING, message_id=message_id)

    @classmethod
    def processed(cls, message_id: str) -> Call:
        return cls(endpoint=Endpoint.PROCESSED, message_id=message_id)

    @classmethod
    def working(cls) -> Call:
        return cls(endpoint=Endpoint.WORKING)

    @classmethod
    def context(cls) -> Call:
        return cls(endpoint=Endpoint.CONTEXT)

    @classmethod
    def next(cls) -> Call:
        return cls(endpoint=Endpoint.NEXT)

    @classmethod
    def page(cls) -> Call:
        return cls(endpoint=Endpoint.PAGE)

    @classmethod
    def of(cls, request: httpx.Request) -> Call | None:
        """The scriptable call a request is, or ``None`` for any other request."""
        path = request.url.path.split("/")
        match (request.method, path[-1]):
            case ("POST", "processing" | "processed" | "failed" as tail):
                return cls(endpoint=Endpoint(tail), message_id=path[-2])
            case ("POST", "activity") if b'"working":true' in request.content.replace(
                b" ", b""
            ):
                return cls.working()
            case ("GET", "context"):
                return cls.context()
            case ("GET", "next"):
                return cls.next()
            case ("GET", "messages") if request.url.params.get("status") is None:
                return cls.page()
        return None

    def __str__(self) -> str:
        return " ".join(filter(None, (self.endpoint, self.message_id)))


def processing_writes(message_ids: Iterable[str]) -> list[str]:
    """The timeline entries of one processing write per message, in order."""
    return [str(Call.processing(message_id)) for message_id in message_ids]


def processed_writes(message_ids: Iterable[str]) -> list[str]:
    """The timeline entries of one processed write per message, in order."""
    return [str(Call.processed(message_id)) for message_id in message_ids]


def run(message_ids: Iterable[str]) -> str:
    """The timeline entry of one model invocation over ``message_ids``."""
    return f"run {' '.join(message_ids)}"


def all_processed(message_ids: Iterable[str]) -> dict[str, str | None]:
    """The delivery states of originals the platform has settled."""
    return dict.fromkeys(message_ids, DeliveryStatus.PROCESSED)


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

    def post(self, *message_ids: str, listed: bool = True) -> list[MessageEvent]:
        """Store the messages and return the WebSocket event for each.

        An unlisted message is one REST and /next omit, such as a thread reply.
        """
        events: list[MessageEvent] = []
        for message_id in message_ids:
            row = self.peer.add_message(message_id, listed=listed)
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

    def script(self, call: Call, *, answer: Answer) -> None:
        """Answer every ``call`` this way until it is scripted again."""
        self.scripts[call] = answer

    def hold(
        self,
        call: Call,
        *,
        answer: Answer = Answer.ACCEPT,
        suppress_cancel: bool = False,
    ) -> Hold:
        """Decide the next ``call`` as ``answer`` but deliver nothing until released."""
        self.holds[call] = hold = Hold(answer=answer, suppress_cancel=suppress_cancel)
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
        if call.endpoint not in POLLS:
            self.journal.append(str(call))
        held = self.holds.pop(call, None)
        answer = held.answer if held else self.scripts.get(call, Answer.ACCEPT)
        response = self.decide(request, call=call, answer=answer)
        if held is not None:
            held.entered.set()
            await held.wait()
        if answer is Answer.LOST:
            raise httpx.ReadTimeout("the response never arrived", request=request)
        return response

    def decide(
        self, request: httpx.Request, *, call: Call, answer: Answer
    ) -> httpx.Response:
        match answer:
            case Answer.ACCEPT | Answer.LOST:
                response = self.peer.answer(request)
                if response.status_code == 200 and call.endpoint in {
                    Endpoint.PROCESSING,
                    Endpoint.FAILED,
                }:
                    for row in self.peer.messages:
                        if row["id"] == call.message_id:
                            row["metadata"]["delivery_status"] = {
                                AGENT_ID: {"status": call.endpoint}
                            }
                return response
            case Answer.REFUSE:
                return httpx.Response(422, json=REFUSAL)
            case Answer.STOP if call.endpoint is Endpoint.WORKING:
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


OFF = Case(name="off-singleton", batching=False)
ON = Case(name="on-singleton", batching=False)
BATCH = Case(name="on-batch", batching=False, size=3)
FIRST, MIDDLE, LAST = (
    replace(BATCH, name=f"batch-{where}", at=at)
    for at, where in enumerate(("first", "middle", "last"))
)
SHAPES = (OFF,)
SINGLETONS = (OFF,)  # one original per turn, whatever the mode
POSITIONS = (OFF,)

# Two originals a recovery pass finds queued.
QUEUED = ("m1", "m2")


@dataclass(frozen=True)
class Drain:
    """How a room shape works through ``QUEUED``: the read a recovery pass makes
    to find them and the model runs it ends in."""

    case: Case
    read: Call
    runs: list[tuple[str, ...]]

    def __str__(self) -> str:
        return str(self.case)


DRAINS = (Drain(case=OFF, read=Call.next(), runs=[("m1",), ("m2",)]),)


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
    name="context-refresh",
    call=Call.context(),
    settings={"enable_context_hydration": True, "context_cache_ttl_seconds": 0},
    warm=True,
)
REPORT = Window(
    name="activity-report",
    call=Call.working(),
    settings={"enable_working_state": True},
)
WINDOWS = (REFRESH, REPORT)


@dataclass(frozen=True)
class Observed:
    """What the platform and the model saw, as a scenario's outcome."""

    statuses: dict[str, str | None]
    runs: list[tuple[str, ...]]
    terminal_writes: list[str]

    @property
    def progress(self) -> tuple[dict[str, str | None], list[tuple[str, ...]]]:
        """The originals' delivery states and the model runs, whatever writes got there."""
        return self.statuses, self.runs

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
            statuses=all_processed(ids),
            runs=[ids] if runs is None else runs,
            terminal_writes=processed_writes(ids),
        )


@dataclass
class Room(Waiting):
    case: Case
    platform: Platform
    model: Model
    ctx: ExecutionContext
    registry: ClaimRegistry
    link: BandLink
    settings: dict[str, Any]
    controls: ControlScript | None = None

    async def arrive(self, *, listed: bool = True) -> None:
        """Post the case's originals and deliver them over the socket together."""
        await self.deliver(*self.platform.post(*self.case.ids, listed=listed))

    async def deliver(self, *events: MessageEvent) -> None:
        """Deliver already-posted messages over the socket."""
        for event in events:
            await self.ctx.on_event(event)

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

    async def leave_write_unconfirmed(self) -> Hold:
        """Run the case's original with its processed write lost, queue the next
        original behind it and hold the retry a recovery pass will make."""
        first, queued = QUEUED
        self.platform.script(Call.processed(first), answer=Answer.LOST)
        await self.arrive()
        await self.quiesce()
        self.platform.script(Call.processed(first), answer=Answer.ACCEPT)
        self.platform.post(queued)
        return self.platform.hold(Call.processed(first))

    async def hold_control_hydration(self) -> Hold:
        """Park the first queued original as the refused, deferred head, then deliver
        the second, a recognized control, and hold the hydration its probe starts.

        The room must be warm with an expired cache, so a probe hydrates again.
        """
        head, control = QUEUED
        self.model.accepting = False
        await self.quiesce()
        await self.deliver(*self.platform.post(head))
        await self.quiesce()
        pending = self.platform.hold(Call.context())
        await self.deliver(*self.platform.post(control))
        await self.reached(pending)
        return pending

    async def settled(self) -> None:
        """Wait until the platform has every original processed."""
        await self.until(lambda: self.platform.statuses == all_processed(self.case.ids))

    def interrupt(self) -> None:
        self.ctx.interrupt()

    async def signal(self, mode: ControlMode) -> None:
        """An ``agent.control`` signal reaching the room through its runtime."""
        runtime = AgentRuntime(self.link, AGENT_ID, self.model.single)
        runtime.executions[ROOM_ID] = self.ctx
        await runtime.handle_control(
            AgentControlPayload(
                mode=mode, scope="room", agent_id=AGENT_ID, room_id=ROOM_ID
            )
        )

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
            self.case,
            link=self.link,
            model=self.model,
            registry=self.registry,
            settings=self.settings,
            controls=self.controls,
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
    *,
    link: BandLink,
    model: Model,
    registry: ClaimRegistry,
    settings: dict[str, Any],
    controls: ControlScript | None = None,
) -> ExecutionContext:
    return ExecutionContext(
        ROOM_ID,
        link,
        model.single,
        config=SessionConfig(**settings),
        agent_id=AGENT_ID,
        on_execute_batch=model.batch if case.batching else None,
        on_message_control=controls.handle if controls else None,
        is_message_control=controls.recognizes if controls else None,
        claim_registry=registry,
    )


@contextlib.asynccontextmanager
async def started_room(
    case: Case,
    *,
    platform: Platform,
    model: Model,
    warm: bool = False,
    controls: ControlScript | None = None,
    **settings: Any,
) -> AsyncIterator[Room]:
    """A started room over ``platform``; ``settings`` adjust its ``SessionConfig``.

    A ``warm`` room has already hydrated its context, as one that served before.
    With ``controls`` the host also recognizes and handles control messages.
    """
    settings = {
        "enable_context_hydration": False,
        "enable_working_state": False,
    } | settings
    registry = ClaimRegistry()
    async with rest_client_over(platform.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        ctx = build_context(
            case,
            link=link,
            model=model,
            registry=registry,
            settings=settings,
            controls=controls,
        )
        if warm:
            await ctx.hydrate()
        await ctx.start()
        room = Room(
            case=case,
            platform=platform,
            model=model,
            ctx=ctx,
            registry=registry,
            link=link,
            settings=settings,
            controls=controls,
        )
        try:
            yield room
        finally:
            model.release.set()
            await room.ctx.stop()
