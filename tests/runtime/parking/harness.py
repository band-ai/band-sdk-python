"""A real agent runtime over a local HTTP peer that scripts terminal-write refusals."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum

import httpx
from band_sdk_core import ClaimRegistry, ParkedReason
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from band.client.streaming import DeliveryStatus
from band.platform.event import MessageEvent, PlatformEvent
from band.platform.message_lifecycle import (
    PROCESSING_ATTEMPT_POINTER,
    VALIDATION_ERROR_CODE,
)
from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.runtime.sessionconfig import DEFAULT_MAX_MESSAGE_RETRIES, SessionConfig
from tests.conftest import make_message_event
from tests.platform.settlement.kit import (
    MISSING_ATTEMPT,
    ROOM_ID,
    Reply,
    SettlementPeer,
    accepted,
    error_body,
    link_over,
)
from tests.runtime.batching.kit import ControlScript, Turns
from tests.runtime.conftest import Waiting
from tests.runtime.helpers import AGENT_ID, EPOCH, SENDER_ID

# An input that arrives independently of the originals the room starts with.
OTHER = "other"

# Inputs that arrive one by one after the room's first turn.
LATER_ARRIVALS = ("x1", "x2", "x3")

# The room's periodic reconciliation interval: the cadence of every paced retry.
RECONCILE_SECONDS = SessionConfig().idle_resync_seconds

# Long enough for two paced retries to have fired and for their outcome to settle.
PAST_TWO_INTERVALS = 2 * RECONCILE_SECONDS + 1

# After this many /next reads at one instant the peer answers "nothing pending",
# so a room spinning on its head fails an assertion instead of hanging.
MAX_NEXT_READS_AT_ONE_INSTANT = 40

# Deleted-message and lost-access refusals are one and the same reply.
NOT_FOUND = Reply.of_json(status=404, payload=error_body(code="not_found"))

# A refusal the SDK cannot classify beyond "not accepted".
REFUSED = Reply.of_json(status=403, payload=error_body(code="refused"))


@dataclass(frozen=True)
class Hangup:
    """A scripted peer that drops the connection instead of answering."""


class Refusal(StrEnum):
    """How the platform refuses a terminal write after the model has worked.

    The first two are the same 404; only whether the room still answers reads
    tells them apart.
    """

    DELETED = "message-deleted"
    ACCESS_LOST = "access-lost"
    REJECTED = "no-processing-attempt"

    @property
    def parked(self) -> str:
        """The reason the runtime records for the intent it keeps."""
        reason = (
            ParkedReason.Rejected if self is Refusal.REJECTED else ParkedReason.NotFound
        )
        return reason.wire_name


# The refusals that park the intent; a deleted message's 404 retires it instead.
PARKED_REFUSALS = (Refusal.ACCESS_LOST, Refusal.REJECTED)

# The refusals that can be lifted: a deleted message never returns.
RELENTING_REFUSALS = (Refusal.ACCESS_LOST, Refusal.REJECTED)


# What the platform may answer a terminal write that proves nothing about the message.
UNCONFIRMED_REPLIES = {
    "generic-validation-422": Reply.of_json(
        status=422,
        payload=error_body(
            code=VALIDATION_ERROR_CODE, details={"/error": ["is invalid"]}
        ),
    ),
    "no-details-422": Reply.of_json(
        status=422, payload=error_body(code=VALIDATION_ERROR_CODE, details={})
    ),
    "database-error-422": Reply.of_json(
        status=422,
        payload=error_body(
            code="database_error", details={PROCESSING_ATTEMPT_POINTER: ["x"]}
        ),
    ),
    "empty-422": Reply(status=422),
    "malformed-422": Reply(status=422, body="{not json"),
    "forbidden-403": REFUSED,
    "unavailable-503": Reply(status=503),
    "dropped-connection": Hangup(),
}


SPARE_RETRIES = 3


class Mode(StrEnum):
    """How the room consumes what is queued when it starts."""

    OFF = "aggregation-off"
    SINGLETON = "aggregation-on-singleton"
    BATCH = "aggregation-on-batch"

    @property
    def batching(self) -> bool:
        return self is not Mode.OFF

    @property
    def queued(self) -> tuple[str, ...]:
        """The originals already waiting when the room starts."""
        return ("m1", "m2") if self is Mode.BATCH else ("m1",)

    @property
    def retries(self) -> int:
        """Off mode runs with spare retries, so only a retained intent, never an
        exhausted retry budget, can keep the model from replaying."""
        return SPARE_RETRIES if self is Mode.OFF else DEFAULT_MAX_MESSAGE_RETRIES


@dataclass(frozen=True)
class Outcome:
    """What the room and the platform did, as the observable record of a scenario.

    ``admitted`` lists each accepted processing write; ``completed`` is what the
    runtime reports as locally settled; ``platform`` is what the platform holds
    as processed or failed.
    """

    model: list[tuple[str, ...]]
    admitted: list[str]
    terminal_writes: list[str]
    retained: dict[str, tuple[str, str | None]]
    completed: list[str]
    platform: dict[str, str]

    def once_each(self) -> Outcome:
        """The same record with each terminal write listed once, however often retried."""
        return replace(self, terminal_writes=list(dict.fromkeys(self.terminal_writes)))

    def with_other_settled(self) -> Outcome:
        """The same record after an independent input ran and settled processed."""
        processed = DeliveryStatus.PROCESSED
        return replace(
            self,
            model=[*self.model, (OTHER,)],
            admitted=[*self.admitted, OTHER],
            terminal_writes=[*self.terminal_writes, f"{OTHER}/{processed}"],
            completed=sorted([*self.completed, OTHER]),
            platform={**self.platform, OTHER: processed},
        )


@dataclass(frozen=True)
class Cell:
    """One mode and the terminal write its turn owes.

    All modes retain terminal intents; only off mode resumes configured model
    retries after a failed write is confirmed.
    """

    mode: Mode
    terminal: DeliveryStatus

    @property
    def id(self) -> str:
        return f"{self.mode}-{self.terminal}"

    @property
    def queued(self) -> tuple[str, ...]:
        return self.mode.queued

    def writes(self) -> list[str]:
        """One terminal write per queued original."""
        return [f"{m}/{self.terminal}" for m in self.queued]

    def after_first_attempt(self, *, parked: str | None) -> Outcome:
        """One turn over the queued originals whose terminal writes were all refused."""
        return Outcome(
            model=[self.queued],
            admitted=list(self.queued),
            terminal_writes=self.writes(),
            retained={m: (self.terminal, parked) for m in self.queued},
            completed=[],
            platform={},
        )

    def after_gone(self) -> Outcome:
        """One turn over originals the platform no longer has: each terminal write
        is sent once, and nothing is retained or held by the platform."""
        return Outcome(
            model=[self.queued],
            admitted=list(self.queued),
            terminal_writes=self.writes(),
            retained={},
            completed=sorted(self.queued),
            platform={},
        )

    def after_refusal(self, refusal: Refusal) -> Outcome:
        """One turn whose terminal writes were all refused as ``refusal`` says."""
        if refusal is Refusal.DELETED:
            return self.after_gone()
        return self.after_first_attempt(parked=refusal.parked)

    def after_acceptance(self, *, rounds: int = 1) -> Outcome:
        """The same single turn once a terminal write for each original was accepted.

        ``rounds`` counts how often the writes were attempted, the last accepted.
        """
        return Outcome(
            model=[self.queued],
            admitted=list(self.queued),
            terminal_writes=self.writes() * rounds,
            retained={},
            completed=sorted(self.queued),
            platform=dict.fromkeys(self.queued, self.terminal),
        )


OFF_PROCESSED = Cell(mode=Mode.OFF, terminal=DeliveryStatus.PROCESSED)

# Off-mode failure has retry continuations and a separate acceptance contract.
OFF_FAILED = Cell(mode=Mode.OFF, terminal=DeliveryStatus.FAILED)

CELLS = (OFF_PROCESSED,)


TERMINALS = (DeliveryStatus.PROCESSED, DeliveryStatus.FAILED)


class RoomPeer(SettlementPeer):
    """One room on the platform: the actionable list, /next and lifecycle writes.

    A terminal write is answered by ``terminal[message_id]`` (default: accepted).
    ``admission[message_id]`` likewise answers its processing write. ``refuse``
    makes the platform change as the model works: a deletion or loss of access
    strikes when a message's terminal write arrives, and lost access answers
    every room route, reads included, with not found. ``writes`` lists every
    lifecycle write as ``"<message>/<action>"``, answered or not.
    """

    def __init__(self) -> None:
        super().__init__()
        self.app = Starlette(
            routes=[Route("/{path:path}", self.serve, methods=["GET", "POST"])]
        )
        self.status: dict[str, str | None] = {}
        self.sent_at: dict[str, str] = {}
        self.terminal: dict[str, Reply | Hangup] = {}
        self.admission: dict[str, Reply] = {}
        self.doomed: dict[str, Refusal] = {}
        self.access_lost = False
        self.posted = 0
        self.terminal_times: list[float] = []
        self.next_read_times: list[float] = []
        self.terminal_replies: list[Reply | Hangup] = []
        self.admitted: list[str] = []

    def post(self, message_id: str) -> MessageEvent:
        """Store a new message and return the event the WebSocket delivers for it."""
        moment = EPOCH + timedelta(seconds=self.posted)
        self.posted += 1
        self.status[message_id] = None
        self.sent_at[message_id] = moment.isoformat()
        return self.event(message_id)

    def event(self, message_id: str) -> MessageEvent:
        """The WebSocket delivery of a message, however often it is repeated."""
        return make_message_event(
            room_id=ROOM_ID,
            msg_id=message_id,
            sender_id=SENDER_ID,
            inserted_at=self.sent_at[message_id],
        )

    def refuse(self, refusal: Refusal, *message_ids: str) -> None:
        """Refuse the terminal writes of ``message_ids`` the way ``refusal`` says."""
        match refusal:
            case Refusal.REJECTED:
                self.script(MISSING_ATTEMPT, *message_ids)
            case _:
                self.doomed.update(dict.fromkeys(message_ids, refusal))

    def script(self, reply: Reply | Hangup | Refusal, *message_ids: str) -> None:
        """Answer the terminal writes of ``message_ids`` with ``reply``."""
        match reply:
            case Refusal():
                self.refuse(reply, *message_ids)
            case _:
                self.terminal.update(dict.fromkeys(message_ids, reply))

    def relent(self) -> None:
        """Accept terminal writes again; a deleted message stays deleted."""
        self.doomed.clear()
        self.terminal.clear()
        self.access_lost = False

    def terminal_writes(self) -> list[str]:
        return [w for w in self.writes if w.endswith(tuple(TERMINALS))]

    def repeated_terminal_writes(self) -> list[str]:
        """The terminal writes attempted more than once, each listed once."""
        counts = Counter(self.terminal_writes())
        return [write for write, count in counts.items() if count > 1]

    def completed(self) -> dict[str, str]:
        """The messages the platform holds as processed or failed for the agent."""
        return {m: s for m, s in self.status.items() if s in TERMINALS}

    def busiest_next_reads(self) -> int:
        """The most /next reads made at any one instant of the loop's clock."""
        return max(Counter(self.next_read_times).values(), default=0)

    def attempt_offsets(self) -> list[float]:
        """When terminal writes were attempted, as distinct seconds after the first."""
        first = self.terminal_times[0]
        return sorted({round(t - first, 3) for t in self.terminal_times})

    async def serve(self, request: Request) -> Response:
        reply = self.answer(request)
        if isinstance(reply, Hangup):
            raise httpx.ConnectError("the peer dropped the connection")
        return Response(
            reply.body, status_code=reply.status, media_type="application/json"
        )

    def answer(self, request: Request) -> Reply | Hangup:
        _, found, tail = request.url.path.partition("/messages")
        match found, request.method, tail.strip("/").split("/"):
            case "/messages", "POST", [message_id, action]:
                return self.write(message_id, DeliveryStatus(action))
            case "/messages", "GET", [""]:
                return self.actionable()
            case "/messages", "GET", ["next"]:
                return self.next_message()
        return Reply.of_json(200, {"data": [], "metadata": {"has_more": False}})

    def actionable(self) -> Reply:
        if self.access_lost:
            return NOT_FOUND
        rows = [
            self.row(m) for m, s in self.status.items() if s != DeliveryStatus.PROCESSED
        ]
        return Reply.of_json(
            200, {"data": rows, "metadata": {"limit": 100, "has_more": False}}
        )

    def next_message(self) -> Reply:
        now = asyncio.get_running_loop().time()
        self.next_read_times.append(now)
        if self.access_lost:
            return NOT_FOUND
        recent = self.next_read_times[-MAX_NEXT_READS_AT_ONE_INSTANT:]
        if len(recent) == MAX_NEXT_READS_AT_ONE_INSTANT and set(recent) == {now}:
            return Reply(204)
        rows = [
            self.row(m) for m, s in self.status.items() if s != DeliveryStatus.PROCESSED
        ]
        return Reply.of_json(200, {"data": rows[0]}) if rows else Reply(204)

    def write(self, message_id: str, action: DeliveryStatus) -> Reply | Hangup:
        self.writes.append(f"{message_id}/{action}")
        if action in TERMINALS:
            self.terminal_times.append(asyncio.get_running_loop().time())
            self.strike(message_id)
        if self.access_lost or message_id not in self.status:
            reply: Reply | Hangup = NOT_FOUND
        elif action is DeliveryStatus.PROCESSING:
            reply = self.admission.get(message_id, accepted(action))
        else:
            reply = self.terminal.get(message_id, accepted(action))
        if action in TERMINALS:
            self.terminal_replies.append(reply)
        if reply == accepted(action):
            self.status[message_id] = action
            if action is DeliveryStatus.PROCESSING:
                self.admitted.append(message_id)
        return reply

    def strike(self, message_id: str) -> None:
        match self.doomed.get(message_id):
            case Refusal.DELETED:
                self.status.pop(message_id, None)
            case Refusal.ACCESS_LOST:
                self.access_lost = True

    def row(self, message_id: str) -> dict[str, object]:
        status = self.status[message_id]
        delivery = {AGENT_ID: {"status": status}} if status else {}
        return {
            "id": message_id,
            "chat_room_id": ROOM_ID,
            "content": message_id,
            "sender_id": SENDER_ID,
            "sender_type": "User",
            "sender_name": "User",
            "message_type": "text",
            "metadata": {"delivery_status": delivery},
            "inserted_at": self.sent_at[message_id],
            "updated_at": self.sent_at[message_id],
        }


@dataclass
class Harness(Waiting):
    """A runtime serving one room against a ``RoomPeer``."""

    peer: RoomPeer
    turns: Turns
    runtime: AgentRuntime
    claims: ClaimRegistry | None = None

    @property
    def execution(self) -> ExecutionContext:
        execution = self.runtime.executions[ROOM_ID]
        assert isinstance(execution, ExecutionContext)
        return execution

    @property
    def serving(self) -> bool:
        return ROOM_ID in self.runtime.executions

    async def join(self) -> None:
        """Join through presence's callback (a real join needs a live socket)
        and wait until the room is serving."""
        await self.runtime.presence.on_room_joined(ROOM_ID, {})
        await self.converged()
        self.claims = self.execution.claims

    async def join_in_outage(self, cell: Cell) -> None:
        """Join while every terminal write goes unconfirmed, and wait until each
        original's has been attempted and the room is quiet."""
        self.peer.script(REFUSED, *cell.queued)
        await self.join()
        await self.attempted_terminal_writes(len(cell.queued))
        await self.elapse(1)

    async def leave(self) -> None:
        """Leave through presence's callback, as join does, and wait for retirement."""
        await self.runtime.presence.on_room_left(ROOM_ID)
        await self.converged()

    async def converged(self) -> None:
        if (pending := self.runtime.rooms.pending(ROOM_ID)) is not None:
            await asyncio.wait({pending})

    async def deliver(self, event: PlatformEvent) -> None:
        await self.runtime.presence.on_room_event(ROOM_ID, event)

    async def attempted_terminal_writes(self, count: int) -> None:
        await self.until(lambda: len(self.peer.terminal_writes()) >= count)

    async def admission_attempted(self, message_id: str) -> None:
        await self.until(lambda: f"{message_id}/processing" in self.peer.writes)

    async def settled(self, message_id: str) -> None:
        await self.until(lambda: message_id in self.peer.completed())

    async def intents_resolved(self) -> None:
        await self.until(lambda: self.retained() == {})

    async def elapse(self, seconds: float) -> None:
        """Let the room's scheduler run for ``seconds`` of the loop's virtual clock."""
        await asyncio.sleep(seconds)

    def retained(self) -> dict[str, tuple[str, str | None]]:
        """Each retained terminal intent as ``(status, parked reason)``."""
        assert self.claims is not None
        return {
            message_id: (pending["status"], pending["parked"])
            for message_id in self.claims.pending_ack_ids(ROOM_ID)
            if (pending := self.claims.pending_ack(ROOM_ID, message_id)) is not None
        }

    def completed(self) -> list[str]:
        assert self.claims is not None
        return sorted(self.claims.completed_ids(ROOM_ID))

    def outcome(self) -> Outcome:
        return Outcome(
            model=list(self.turns.ids),
            admitted=list(self.peer.admitted),
            terminal_writes=self.peer.terminal_writes(),
            retained=self.retained(),
            completed=self.completed(),
            platform=self.peer.completed(),
        )


@asynccontextmanager
async def harness_over(
    peer: RoomPeer, cell: Cell, *, controls: ControlScript | None = None
) -> AsyncIterator[Harness]:
    """A runtime for ``cell``; its model fails exactly when the cell's terminal is failed.

    With ``controls`` the host also handles control messages, so the room scans
    every arrival for them.
    """
    async with peer.client() as rest:
        link = link_over(rest)
        fails = cell.terminal is DeliveryStatus.FAILED
        turns = Turns(fail_on=set(cell.queued) if fails else set())
        runtime = AgentRuntime(
            link,
            AGENT_ID,
            turns.single,
            on_execute_batch=turns.batch if cell.mode.batching else None,
            on_message_control=controls.handle if controls else None,
            is_message_control=controls.recognizes if controls else None,
            session_config=SessionConfig(
                max_message_retries=cell.mode.retries,
                enable_context_hydration=False,
                enable_working_state=False,
                report_turn_failures_to_room=False,
            ),
        )
        try:
            yield Harness(peer, turns, runtime)
        finally:
            await runtime.stop(timeout=1)
