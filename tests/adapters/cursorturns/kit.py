"""Doubles and helpers for the Cursor room-turn tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from acp.schema import AllowedOutcome, ToolCallUpdate

from band.adapters.cursor_acp import CursorACPAdapter, CursorTurn
from band.client.streaming import ControlMode
from band.core.exceptions import InputNotAccepted
from band.core.retention import RetainedRoom
from band.integrations.acp.cursor import ROOM_COMMAND, CursorCommandWord
from tests.integrations.acp.acp_toolkit.agent import (
    CANCEL_RECORD,
    FakeACPAgent,
    allow_or_reject,
)
from tests.integrations.acp.acp_toolkit.harness import (
    AcpSession,
    TranscriptTools,
    live_message,
)
from tests.integrations.acp.acp_toolkit.harness import decision_token as prompt_token

ROOM_A = "room-A"
ROOM_B = "room-B"

LIST_DECISIONS = f"{ROOM_COMMAND} {CursorCommandWord.DECISIONS}"

# One person writing twice around another: the senders of ``deliver``'s batch.
BATCH_INPUT_SENDERS = ("alice", "bob", "alice")

# The coroutine a room's owner runs as its detached work, which a Cursor turn is.
OWNED_TASK_NAME = f"{RetainedRoom.start_owned.__qualname__}.<locals>.run"

# Check pending work immediately; tests hold its lifecycle with explicit gates.
SETTLE_S = 0

# A hang in a peer or a held room fails a test within this, not at the suite timeout.
WAIT_S = 5.0

BusyKind = Literal["prompt", "approval"]
TurnEnd = Callable[[CursorACPAdapter, str], Awaitable[None]]


@dataclass
class BusyProvider:
    """A peer whose chosen prompts (1-based) stay in flight; the rest return at once.

    ``prompt`` parks inside the prompt itself until ``finish`` is set.
    ``approval`` parks on a Cursor permission request, so the delivery worker
    is released while the provider keeps running.
    """

    agent: FakeACPAgent
    kind: BusyKind
    parked: asyncio.Event
    finish: asyncio.Event


def busy_provider(
    kind: BusyKind, *, on_prompts: Collection[int] = (1,)
) -> BusyProvider:
    parked = asyncio.Event()
    finish = asyncio.Event()
    agent = FakeACPAgent()

    @agent.on_prompt
    async def run(peer: FakeACPAgent, session_id: str) -> None:
        if len(peer.prompts) not in on_prompts:
            return
        parked.set()
        match kind:
            case "prompt":
                await finish.wait()
            case "approval":
                response = await peer.ask_permission(
                    session_id,
                    ToolCallUpdate(tool_call_id="tc-1", title="shell"),
                    allow_or_reject(),
                )
                peer.approved = isinstance(response.outcome, AllowedOutcome)

    return BusyProvider(agent=agent, kind=kind, parked=parked, finish=finish)


def cursor_adapter(session: AcpSession) -> CursorACPAdapter:
    assert isinstance(session.adapter, CursorACPAdapter)
    return session.adapter


async def stop(adapter: CursorACPAdapter, room_id: str) -> None:
    await adapter.on_interrupt(room_id, ControlMode.STOP)


async def interrupt(adapter: CursorACPAdapter, room_id: str) -> None:
    await adapter.on_interrupt(room_id, ControlMode.INTERRUPT)


async def retire(adapter: CursorACPAdapter, room_id: str) -> None:
    await adapter.on_cleanup(room_id)


def active_turn(session: AcpSession, room: str = ROOM_A) -> CursorTurn:
    turn = cursor_adapter(session)._turns.get(room)
    assert turn is not None, f"{room} has no accepted Cursor turn"
    return turn


def provider_task(session: AcpSession, room: str = ROOM_A) -> asyncio.Task[None]:
    """The task running the room's accepted turn against the provider."""
    task = active_turn(session, room).task
    assert task is not None, f"{room}'s turn has no provider task"
    return task


async def terminated(session: AcpSession, turn: CursorTurn) -> None:
    """Wait for the turn's provider task and its backend stop to end, without
    cancelling either."""
    assert turn.task is not None
    async with asyncio.timeout(WAIT_S):
        await asyncio.wait({turn.task})
        await cursor_adapter(session).backend_turns.settled(turn.room.room_id)


def cancels_sent(agent: FakeACPAgent) -> list[str]:
    """The session each ``session/cancel`` the client sent named, in order."""
    return [
        record.removeprefix(f"{CANCEL_RECORD} ")
        for record in agent.lifecycle
        if record.startswith(CANCEL_RECORD)
    ]


def turn_tools(session: AcpSession, room: str = ROOM_A) -> TranscriptTools:
    """The tools the room's active turn was admitted with, where its notices land."""
    tools = active_turn(session, room).tools
    assert isinstance(tools, TranscriptTools)
    return tools


async def deliver(
    session: AcpSession, content: str, *, room: str, batch: bool = False
) -> None:
    """Deliver ``content`` through the singleton entry, or through the batch entry
    as one input per ``BATCH_INPUT_SENDERS``."""
    if not batch:
        await session.send(content, room=room)
        return
    await session.send_batch(
        *(
            live_message(f"{content} {n}", room_id=room, sender_id=sender)
            for n, sender in enumerate(BATCH_INPUT_SENDERS, start=1)
        ),
        room=room,
    )


async def occupy(
    session: AcpSession,
    provider: BusyProvider,
    *,
    room: str = ROOM_A,
    batch: bool = False,
) -> asyncio.Task[Any]:
    """Start a turn in ``room`` and return once the peer holds its prompt.

    The returned task is the delivery worker's turn call: it ends with the turn
    for ``prompt`` and as soon as the decision opens for ``approval``.
    """
    provider.parked.clear()
    sending = asyncio.create_task(deliver(session, "go", room=room, batch=batch))
    try:
        async with asyncio.timeout(WAIT_S):
            await provider.parked.wait()
            if provider.kind == "approval":
                await sending
    except BaseException:
        provider.finish.set()
        sending.cancel()
        await asyncio.gather(sending, return_exceptions=True)
        raise
    return sending


async def finish_turn(provider: BusyProvider, sending: asyncio.Task[Any]) -> None:
    """Let the peer end the occupying prompt and wait for the delivery to return,
    which frees the room."""
    provider.finish.set()
    async with asyncio.timeout(WAIT_S):
        await sending


async def is_accepted(session: AcpSession, *, room: str, batch: bool = False) -> bool:
    """Whether Cursor takes ``room``'s next input; a taken input runs its turn."""
    try:
        async with asyncio.timeout(WAIT_S):
            await deliver(session, "next", room=room, batch=batch)
    except InputNotAccepted:
        return False
    return True


async def command(
    session: AcpSession, content: str, *, sender_id: str, room: str = ROOM_A
) -> tuple[bool, TranscriptTools]:
    """Hand ``content`` to Cursor's control hook as the runtime does for a
    command; return whether it was consumed and the tools its reply went through."""
    tools = TranscriptTools()
    consumed = await cursor_adapter(session).try_handle_control(
        live_message(content, room_id=room, sender_id=sender_id), tools
    )
    return consumed, tools


def decision_token(session: AcpSession) -> str:
    """The token in the decision prompt the last delivery posted."""
    return prompt_token(session.last_reply.texts[-1])


def select_command(token: str, option: str = "allow-1") -> str:
    """The room command that picks ``option`` for the decision ``token``."""
    return f"{ROOM_COMMAND} {CursorCommandWord.SELECT} {token} {option}"


def deny_command(token: str) -> str:
    """The room command that denies the decision ``token``."""
    return f"{ROOM_COMMAND} {CursorCommandWord.DENY} {token}"


@contextmanager
def refusing_turn_tasks() -> Iterator[None]:
    """Make the loop refuse to create a room's owned task, as a Cursor turn is."""
    loop = asyncio.get_running_loop()
    previous = loop.get_task_factory()

    def factory(
        loop: asyncio.AbstractEventLoop, coro: Any, **kwargs: Any
    ) -> asyncio.Future[Any]:
        if coro.__qualname__ == OWNED_TASK_NAME:
            raise RuntimeError("the loop refuses new tasks")
        return asyncio.Task(coro, loop=loop, **kwargs)

    loop.set_task_factory(factory)
    try:
        yield
    finally:
        loop.set_task_factory(previous)
