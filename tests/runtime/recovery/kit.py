"""A stateful one-room platform peer and the real runtime wired to it.

The runtime under test is an ``AgentRuntime`` building a real
``ExecutionContext`` per room, driving a real ``Agent`` host with a scripted
adapter, over a ``BandLink`` whose REST client is the pinned Fern client
talking HTTP to ``RoomPeer``. Only the model is scripted.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from itertools import product
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import Response

from band.client.rest import NO_RETRY_REQUEST_OPTIONS, AsyncRestClient
from band.client.streaming import DeliveryStatus
from band.core.protocols import TURN_FAILURE_PROVIDER, AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.turn import judge_detached_turn
from band.platform.event import MessageEvent
from band.runtime.execution import ExecutionContext
from band.runtime.messages import PlatformMessage
from band.runtime.runtime import AgentRuntime
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event
from tests.platform.settlement.kit import (
    MISSING_ATTEMPT,
    ROOM_ID,
    Reply,
    SettlementPeer,
    accepted,
    link_over,
)
from tests.runtime.helpers import AGENT_ID, EPOCH, SENDER_ID, agent_hooks

# Requests the generated client spends on one logical write that keeps answering 5xx.
HTTP_ATTEMPTS = NO_RETRY_REQUEST_OPTIONS["max_retries"] + 1
# Fake seconds that let every non-timer-driven step finish; far below the
# room's reconciliation interval yet above the 15.6 ms loop clock tick of
# Windows before Python 3.13, which treats any shorter sleep as already due.
DRAIN_SECONDS = 0.05
RESYNC_SECONDS = SessionConfig().idle_resync_seconds
# Text a failing model raises with; it must never reach the platform.
SECRET = "secret-token-in-the-exception"
# What the scripted model says when it replies.
REPLY_TEXT = "done"
# Content of a delivery the scripted adapter recognises and consumes as a control.
CONTROL = "approve"
# An ordinary delivery that arrives while earlier work is still owed.
FOLLOWER = "follower"

TERMINAL = frozenset({DeliveryStatus.PROCESSED, DeliveryStatus.FAILED})
LIFECYCLE = frozenset({DeliveryStatus.PROCESSING, *TERMINAL})
ROOM_PATH = f"/api/v1/agent/chats/{ROOM_ID}"


class Fault(StrEnum):
    """What happens to a terminal write the platform is asked for."""

    LOST = "lost"  # applied, but its response never reaches the agent
    REFUSED = "refused"  # not applied: 503, which the client retries inside the call

    @property
    def applied(self) -> bool:
        return self is Fault.LOST

    @property
    def requests_per_write(self) -> int:
        """HTTP requests the generated client spends on one logical write."""
        return HTTP_ATTEMPTS if self is Fault.REFUSED else 1


class Shape(StrEnum):
    OFF = "off"  # aggregation off: every delivery is its own turn
    SINGLETON = "on-singleton"  # aggregation on, one original waiting
    BATCH = "on-batch"  # aggregation on, three originals taken in one turn

    @property
    def batching(self) -> bool:
        return self is not Shape.OFF

    @property
    def members(self) -> tuple[str, ...]:
        return ("m1", "m2", "m3") if self is Shape.BATCH else ("m1",)


class Ending(StrEnum):
    """How the scripted model ends a turn."""

    DECLINES = "declines"  # no_reply: the turn completes
    RAISES = "raises"  # posts a reply, then raises
    SILENT = "silent"  # only reads state, so the judge reports a missing reply
    DETACHES = "detaches"  # releases the turn at once and fails later on its own

    @property
    def intent(self) -> DeliveryStatus:
        """The terminal write the runtime owes its originals."""
        failing = {Ending.RAISES, Ending.SILENT}
        return DeliveryStatus.FAILED if self in failing else DeliveryStatus.PROCESSED


# Default-budget failures finish the input; configured retries are covered by
# the continuation suite.
TURNS = [(Shape.OFF, Ending.DECLINES), (Shape.OFF, Ending.RAISES)]


class Route(StrEnum):
    BACKLOG = "backlog"  # stored before the room starts; REST delivers it
    LIVE = "live"  # the WebSocket pushes it to a running room


class Entrance(StrEnum):
    """The ways a room comes back to an original whose terminal write is owed."""

    RECONCILIATION = "reconciliation"  # a resync after a live delivery
    BACKLOG = "backlog"  # a resync over a REST-delivered original
    CONTROL_PROBE = "control-probe"  # an unrelated arrival scans for controls
    LIVE_DUPLICATE = "live-duplicate"  # the WebSocket delivers the original again

    @property
    def route(self) -> Route:
        match self:
            case Entrance.RECONCILIATION | Entrance.LIVE_DUPLICATE:
                return Route.LIVE
            case _:
                return Route.BACKLOG

    @property
    def paced(self) -> bool:
        """Whether the entrance merely touches the original, so a retry within the
        reconciliation interval of its last attempt waits for the next pass.

        A resync is requested, and a requested pass retries at once.
        """
        return self in {Entrance.CONTROL_PROBE, Entrance.LIVE_DUPLICATE}

    def finds_the_original(self, fault: Fault) -> bool:
        """Whether the entrance can see an original whose terminal write is owed.

        A resync always reads the owed writes from the room's own record. A control
        probe or a duplicate delivery only meets an original the room still holds
        back: a probe through the platform's actionable list, which lists it unless
        the platform already applied the write; a duplicate through the payload the
        room retained from the live push.
        """
        return not (self is Entrance.CONTROL_PROBE and fault.applied)


def entrance_cases(*, paced: bool) -> list[tuple[Shape, Ending, Entrance, Fault]]:
    """Every (turn, entrance, fault) where a paced or a requesting entrance meets
    an original that is owed a terminal write."""
    return [
        (shape, ending, entrance, fault)
        for (shape, ending), entrance, fault in product(TURNS, Entrance, Fault)
        if entrance.paced is paced and entrance.finds_the_original(fault)
    ]


@dataclass
class Row:
    id: str
    inserted_at: datetime
    status: DeliveryStatus | None = None


class RoomPeer(SettlementPeer):
    """One room's platform: stored deliveries with this agent's status.

    Lifecycle writes follow the documented platform contract: ``processing``
    is idempotent while processing and reopens a terminal message; a terminal
    write needs an existing attempt and can overwrite its terminal status.
    The SDK, not the platform, must preserve the first terminal intent.
    ``fail_terminals`` makes terminal writes fail until ``heal``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.rows: dict[str, Row] = {}
        self.faults: dict[str, Fault] = {}
        self.outage: Fault | None = None
        self.errors: dict[str, list[str]] = {}
        self.reopened: list[str] = []
        self.rejected: list[str] = []
        self.replies: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        for path, endpoint in (
            ("/messages", self.list_rows),
            ("/messages/next", self.next_row),
            ("/participants", self.participants),
        ):
            self.app.add_route(ROOM_PATH + path, endpoint, methods=["GET"])

    # --- test-side state -------------------------------------------------

    def post(self, message_id: str) -> Row:
        """Store a message addressed to the agent, in arrival order."""
        row = Row(message_id, EPOCH + timedelta(seconds=len(self.rows)))
        self.rows[message_id] = row
        return row

    def event(self, message_id: str) -> MessageEvent:
        """The WebSocket push for a stored message."""
        return make_message_event(
            room_id=ROOM_ID,
            msg_id=message_id,
            content=message_id,
            sender_id=SENDER_ID,
            inserted_at=self.rows[message_id].inserted_at.isoformat(),
        )

    def fail_terminals(self, fault: Fault, *message_ids: str) -> None:
        """Fault these messages' terminal writes; every message by default."""
        if message_ids:
            self.faults.update(dict.fromkeys(message_ids, fault))
        else:
            self.outage = fault

    def heal(self) -> None:
        self.faults.clear()
        self.outage = None

    @property
    def statuses(self) -> dict[str, DeliveryStatus | None]:
        return {message_id: row.status for message_id, row in self.rows.items()}

    def written_since(self, mark: int) -> dict[str, list[str]]:
        """Lifecycle writes received after ``mark`` writes, by message."""
        grouped: dict[str, list[str]] = {}
        for write in self.writes[mark:]:
            message_id, action = write.split("/")
            grouped.setdefault(message_id, []).append(action)
        return grouped

    @property
    def write_kinds(self) -> set[str]:
        """Every kind of lifecycle write the platform has received."""
        return {write.split("/")[1] for write in self.writes}

    def terminal_requests(self, message_id: str) -> int:
        """HTTP requests the platform received to settle ``message_id``."""
        return sum(
            write in {f"{message_id}/{action}" for action in TERMINAL}
            for write in self.writes
        )

    @property
    def reports(self) -> list[str]:
        """Failure reports the runtime posted to the room, as their text."""
        return [
            event["content"]
            for event in self.events
            if (event["metadata"] or {}).get("failure", {}).get("provider")
            == TURN_FAILURE_PROVIDER
        ]

    # --- platform routes -------------------------------------------------

    async def respond(self, request: Request) -> Response:
        *_, target, tail = request.url.path.split("/")
        match tail:
            case "messages":
                return await self.create_reply(request)
            case "events":
                return await self.create_event(request)
            case _:
                return await self.write(target, DeliveryStatus(tail), request)

    async def write(
        self, message_id: str, action: DeliveryStatus, request: Request
    ) -> Response:
        self.writes.append(f"{message_id}/{action}")
        row = self.rows[message_id]
        if action is DeliveryStatus.PROCESSING:
            self.reopen(row)
            return self.reply_with(accepted(action))
        if action is DeliveryStatus.FAILED:
            error = (await request.json())["error"]
            self.errors.setdefault(message_id, []).append(error)
        if row.status not in LIFECYCLE:
            self.rejected.append(f"{message_id}/{action}")
            return self.reply_with(MISSING_ATTEMPT)
        match self.faults.get(message_id, self.outage):
            case Fault.REFUSED:
                return Response(status_code=503)
            case Fault.LOST:
                row.status = action
                raise httpx.ReadError("the response was lost")
            case _:
                row.status = action
                return self.reply_with(accepted(action))

    def reopen(self, row: Row) -> None:
        if row.status in TERMINAL:
            self.reopened.append(row.id)
        row.status = DeliveryStatus.PROCESSING

    @staticmethod
    def reply_with(reply: Reply) -> Response:
        return Response(
            reply.body, status_code=reply.status, media_type="application/json"
        )

    async def create_reply(self, request: Request) -> Response:
        self.replies.append((await request.json())["message"])
        return self.reply_with(
            Reply.of_json(
                status=201,
                payload={
                    "data": {
                        "id": f"reply-{len(self.replies)}",
                        "recipients": [
                            {"id": SENDER_ID, "handle": "alice", "name": "Alice"}
                        ],
                        "success": True,
                    }
                },
            )
        )

    async def create_event(self, request: Request) -> Response:
        body = (await request.json())["event"]
        self.events.append(body)
        return self.reply_with(
            Reply.of_json(
                status=201,
                payload={
                    "data": {
                        "id": f"event-{len(self.events)}",
                        "message_type": body["message_type"],
                        "success": True,
                    }
                },
            )
        )

    async def participants(self, request: Request) -> Response:
        return self.reply_with(
            Reply.of_json(
                status=200,
                payload={
                    "data": [
                        {
                            "id": SENDER_ID,
                            "name": "Alice",
                            "type": "User",
                            "handle": "alice",
                            "role": "member",
                            "status": "active",
                        },
                        {
                            "id": AGENT_ID,
                            "name": "Agent",
                            "type": "Agent",
                            "handle": "agent",
                            "role": "member",
                            "status": "active",
                        },
                    ]
                },
            )
        )

    def actionable(self) -> list[Row]:
        """Everything this agent has not processed, oldest first."""
        return sorted(
            (r for r in self.rows.values() if r.status is not DeliveryStatus.PROCESSED),
            key=lambda row: (row.inserted_at, row.id),
        )

    async def list_rows(self, request: Request) -> Response:
        rows = self.actionable()
        if (cursor := request.query_params.get("cursor")) is not None:
            rows = rows[[row.id for row in rows].index(cursor) + 1 :]
        limit = int(request.query_params.get("limit", "100"))
        page, rest = rows[:limit], rows[limit:]
        return self.reply_with(
            Reply.of_json(
                status=200,
                payload={
                    "data": [self.wire(row) for row in page],
                    "metadata": {
                        "limit": limit,
                        "has_more": bool(rest),
                        "next_cursor": page[-1].id if rest else None,
                    },
                },
            )
        )

    async def next_row(self, request: Request) -> Response:
        if not (rows := self.actionable()):
            return Response(status_code=204)
        return self.reply_with(
            Reply.of_json(status=200, payload={"data": self.wire(rows[0])})
        )

    @staticmethod
    def wire(row: Row) -> dict[str, Any]:
        statuses = {AGENT_ID: {"status": row.status}} if row.status else {}
        return {
            "id": row.id,
            "chat_room_id": ROOM_ID,
            "content": row.id,
            "sender_id": SENDER_ID,
            "sender_type": "User",
            "sender_name": "Alice",
            "message_type": "text",
            "metadata": {
                "mentions": [{"id": AGENT_ID, "username": "agent"}],
                "delivery_status": statuses,
            },
            "inserted_at": row.inserted_at.isoformat(),
            "updated_at": row.inserted_at.isoformat(),
        }


class ScriptedAdapter(SimpleAdapter[Any]):
    """A batch-capable adapter whose model is a script: it records every turn."""

    supports_batch_input = True

    def __init__(self) -> None:
        super().__init__()
        self.ending = Ending.DECLINES
        self.turns: list[tuple[str, ...]] = []
        self.controls: list[str] = []
        self.gate: asyncio.Event | None = None
        self.late_failure = asyncio.Event()
        self.detached: list[asyncio.Task[None]] = []

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        await self.on_turn(
            (msg,),
            tools,
            history,
            participants_msg,
            contacts_msg,
            is_session_bootstrap=is_session_bootstrap,
            room_id=room_id,
        )

    async def on_turn(
        self,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        history: Any,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        self.turns.append(tuple(message.id for message in messages))
        if self.gate is not None:
            await self.gate.wait()
        match self.ending:
            case Ending.DECLINES:
                await tools.no_reply("scripted")
            case Ending.RAISES:
                await tools.send_message(REPLY_TEXT, mentions=[SENDER_ID])
                raise RuntimeError(SECRET)
            case Ending.SILENT:
                await tools.get_participants()
            case Ending.DETACHES:
                tools.turn.detach()
                self.detached.append(
                    asyncio.create_task(self.fail_later(tools, room_id))
                )

    async def fail_later(self, tools: AgentToolsProtocol, room_id: str) -> None:
        """A released turn that ends having done nothing, judged at its real end."""
        await self.late_failure.wait()
        await judge_detached_turn(tools, room_id=room_id)

    def is_control_message(self, msg: PlatformMessage) -> bool:
        return msg.content == CONTROL

    async def try_handle_control(
        self, msg: PlatformMessage, tools: AgentToolsProtocol
    ) -> bool:
        if msg.content != CONTROL:
            return False
        self.controls.append(msg.id)
        return True


@dataclass
class Harness:
    """The real runtime over ``peer``, driven one recovery step at a time."""

    peer: RoomPeer
    adapter: ScriptedAdapter
    runtime: AgentRuntime
    shape: Shape
    healed_at: int = 0

    @property
    def ctx(self) -> ExecutionContext:
        room = self.runtime.executions[ROOM_ID]
        if not isinstance(room, ExecutionContext):
            raise TypeError(f"room {ROOM_ID} is not served by an ExecutionContext")
        return room

    @property
    def members(self) -> tuple[str, ...]:
        return self.shape.members

    async def start(self, route: Route) -> None:
        """Deliver the shape's originals and let the room finish what it can."""
        if route is Route.BACKLOG:
            self.store_members()
            await self.runtime.rooms.admit(ROOM_ID)
            await self.drain()
            return
        await self.runtime.rooms.admit(ROOM_ID)
        await self.drain()
        self.store_members()
        await self.push(*self.members)

    def store_members(self) -> None:
        for message_id in self.members:
            self.peer.post(message_id)

    async def push(self, *message_ids: str) -> None:
        """The WebSocket delivers these stored messages, in order, together."""
        for message_id in message_ids:
            await self.ctx.on_event(self.peer.event(message_id))
        await self.drain()

    async def drain(self) -> None:
        """Let every step that does not wait on a timer finish."""
        await asyncio.sleep(DRAIN_SECONDS)

    async def advance(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def heal(self) -> None:
        """End the outage; ``recovery`` reads what the platform saw after it."""
        self.peer.heal()
        self.healed_at = len(self.peer.writes)

    @property
    def recovery(self) -> dict[str, list[str]]:
        """What the platform received for the originals once the outage ended."""
        written = self.peer.written_since(self.healed_at)
        return {m: written[m] for m in self.members if m in written}

    @property
    def model_turns(self) -> list[tuple[str, ...]]:
        """Every model invocation over the originals."""
        return [t for t in self.adapter.turns if set(t) & set(self.members)]

    @property
    def recovered_kinds(self) -> set[str]:
        """Every kind of write the platform received once the outage ended."""
        return {kind for kinds in self.recovery.values() for kind in kinds}

    @property
    def member_statuses(self) -> dict[str, DeliveryStatus | None]:
        return {m: s for m, s in self.peer.statuses.items() if m in self.members}

    async def outage_then(
        self, fault: Fault, entrance: Entrance, *, passes: int = 0
    ) -> None:
        """Run the turn under an outage of every terminal write, let ``passes``
        reconciliation intervals go by, end the outage and come back to the
        originals through ``entrance``."""
        self.peer.fail_terminals(fault)
        await self.start(entrance.route)
        for _ in range(passes):
            await self.advance(RESYNC_SECONDS)
        self.heal()
        await self.recover(entrance)

    async def recover(self, entrance: Entrance) -> None:
        match entrance:
            case Entrance.RECONCILIATION | Entrance.BACKLOG:
                await self.ctx.request_resync()
            case Entrance.CONTROL_PROBE:
                self.peer.post(FOLLOWER)
                await self.push(FOLLOWER)
            case Entrance.LIVE_DUPLICATE:
                await self.push(self.members[0])
        await self.drain()

    async def replace_room(self) -> ExecutionContext:
        """Retire the room's execution and start its replacement."""
        await self.runtime.rooms.destroy(ROOM_ID)
        await self.runtime.rooms.admit(ROOM_ID)
        await self.drain()
        return self.ctx


def wired_harness(
    peer: RoomPeer,
    adapter: ScriptedAdapter,
    rest: AsyncRestClient,
    shape: Shape,
    *,
    config: SessionConfig | None = None,
) -> Harness:
    """The real runtime for ``shape`` over ``peer``'s REST surface."""
    runtime = AgentRuntime(
        link=link_over(rest),
        agent_id=AGENT_ID,
        session_config=config
        or SessionConfig(
            enable_context_hydration=False,
            enable_working_state=False,
        ),
        **agent_hooks(adapter),
    )
    return Harness(peer=peer, adapter=adapter, runtime=runtime, shape=shape)
