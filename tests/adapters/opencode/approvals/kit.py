"""Doubles and builders shared by the OpenCode approval tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal, cast

import pytest

from band.adapters.opencode import OpencodeAdapterConfig
from band.adapters.opencode.approvals.pending import (
    PendingAsks,
    PendingPermission,
    PendingQuestion,
)
from band.adapters.opencode.approvals.ports import ApprovalPorts
from band.adapters.opencode.approvals.room import RoomApprovals
from band.adapters.opencode.approvals.wording import (
    DECLINED_QUESTION_ANSWER,
    REJECTED_PERMISSION_FEEDBACK,
)
from band.core.protocols import AgentToolsProtocol
from band.integrations.opencode import (
    ApprovalReply,
    OpencodeClientProtocol,
    OpencodeQuestion,
)
from band.testing import FakeAgentTools
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.opencode.helpers import (
    AskFactory,
    FakeOpencodeClient,
    declined_questions,
)


def asks_with(
    *,
    permissions: tuple[str, ...] = (),
    questions: tuple[str, ...] = (),
    known_permissions: frozenset[str] = frozenset(),
    known_questions: frozenset[str] = frozenset(),
) -> PendingAsks:
    """Asks holding the named open requests, plus ids that were asked and resolved."""
    asks = PendingAsks(set(known_permissions), set(known_questions))
    for request_id in permissions:
        pending = PendingPermission(request_id, "bash", ["ls"])
        asks.permissions.register_keyed(pending, key=request_id)
        asks.known_permission_ids.add(request_id)
    for request_id in questions:
        pending_question = PendingQuestion(
            request_id, [OpencodeQuestion(question="Which?")]
        )
        asks.questions.register_keyed(pending_question, key=request_id)
        asks.known_question_ids.add(request_id)
    return asks


#: A seeded participant these tests mention -- shared so a mention-handles
#: assertion always resolves against the same roster entry.
ALICE_PARTICIPANT = {
    "id": "user-1",
    "handle": "@alice",
    "role": "member",
    "status": "active",
    "type": "User",
}


ReplyOperation = Literal["permission", "question", "reject"]


def approval_phases(caplog: pytest.LogCaptureFixture, logger_name: str) -> list[str]:
    """The approval lifecycle as logged: each record's text before its fields."""
    return [
        record.getMessage().partition(" room=")[0]
        for record in caplog.records
        if record.name == logger_name
    ]


@dataclass
class ReplyGate:
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)


class BlockingReplyClient(FakeOpencodeClient):
    """Holds the first reply request of each named operation in flight --
    before it reaches OpenCode -- so a test can interleave room traffic or
    teardown with it. Later requests pass straight through."""

    def __init__(self, *operations: ReplyOperation) -> None:
        super().__init__()
        self._gates = {operation: ReplyGate() for operation in operations}

    @asynccontextmanager
    async def held(
        self, operation: ReplyOperation, reply: Awaitable[bool] | None = None
    ) -> AsyncIterator[asyncio.Future[bool] | None]:
        """Start ``reply`` (or await whatever sends ``operation`` next), keep
        its request in flight for the block's body, then let it finish."""
        gate = self._gates[operation]
        task = None if reply is None else asyncio.ensure_future(reply)
        await gate.started.wait()
        try:
            yield task
        finally:
            gate.release.set()
        if task is not None:
            await task

    async def _block(self, operation: ReplyOperation) -> None:
        if (gate := self._gates.get(operation)) is None or gate.started.is_set():
            return
        gate.started.set()
        await gate.release.wait()

    async def reply_permission(
        self, permission_id: str, *, reply: ApprovalReply, message: str | None = None
    ) -> None:
        await self._block("permission")
        await super().reply_permission(permission_id, reply=reply, message=message)

    async def reply_question(
        self, request_id: str, *, answers: list[list[str]]
    ) -> None:
        declined = all(answer == [DECLINED_QUESTION_ANSWER] for answer in answers)
        await self._block("reject" if declined else "question")
        await super().reply_question(request_id, answers=answers)


class FailingReplyClient(FakeOpencodeClient):
    async def reply_permission(
        self, permission_id: str, *, reply: ApprovalReply, message: str | None = None
    ) -> None:
        raise RuntimeError("permission reply failed")

    async def reply_question(
        self, request_id: str, *, answers: list[list[str]]
    ) -> None:
        raise RuntimeError("question reply failed")


async def ignore_abort() -> None:
    return None


def make_room_approvals(
    client: FakeOpencodeClient,
    *,
    tools: FakeAgentTools | None = None,
    release_turn_wait: Callable[[], None] = lambda: None,
    fail_turn: Callable[[str], None] = lambda _message: None,
    abort_session: Callable[[], Awaitable[None]] = ignore_abort,
    config: OpencodeAdapterConfig | None = None,
) -> RoomApprovals:
    tools = tools if tools is not None else FakeAgentTools(participants=ROOM_MEMBERS)
    return RoomApprovals(
        config or OpencodeAdapterConfig(),
        ApprovalPorts(
            room_id="room-1",
            client=lambda: cast(OpencodeClientProtocol, client),
            tools=lambda: cast(AgentToolsProtocol, tools),
            turn_mentions=lambda: [{"id": ALICE_PARTICIPANT["id"]}],
            release_turn_wait=release_turn_wait,
            fail_turn=fail_turn,
            abort_session=abort_session,
            is_own_band_tool=lambda _permission: False,
        ),
    )


DECLINED = {
    "permission_id": "req-1",
    "reply": "reject",
    "message": REJECTED_PERMISSION_FEEDBACK,
}


@dataclass(frozen=True)
class ExpiryCase:
    """How one kind of ask is raised, and what its expiry sends OpenCode."""

    operation: ReplyOperation
    raise_ask: Callable[[RoomApprovals, AskFactory], Awaitable[None]]
    sent: Callable[[FakeOpencodeClient], list[str]]
    expired_reply: str


EXPIRY_CASES = {
    "permission": ExpiryCase(
        operation="permission",
        raise_ask=lambda room, asks: room.on_permission_asked(asks.permission("ask-1")),
        sent=lambda client: [r["reply"] for r in client.permission_replies],
        expired_reply="reject",
    ),
    "question": ExpiryCase(
        operation="reject",
        raise_ask=lambda room, asks: room.on_question_asked(asks.question("ask-1")),
        sent=declined_questions,
        expired_reply="ask-1",
    ),
}
