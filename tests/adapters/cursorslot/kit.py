"""Doubles and helpers for the Cursor provider-slot tests."""

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
from band.integrations.acp.client_runtime import ACPRuntime
from band.integrations.acp.cursor import ROOM_COMMAND, CursorCommandWord
from tests.adapters.test_cursor_acp_adapter import decision_token as prompt_token
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent, Gate, allow_or_reject
from tests.integrations.acp.acp_toolkit.harness import (
    AcpSession,
    TranscriptTools,
    live_message,
)

ROOM_A = "room-A"
ROOM_B = "room-B"
ROOM_C = "room-C"

# One person writing twice around another: the senders of ``deliver``'s batch.
BATCH_INPUT_SENDERS = ("alice", "bob", "alice")

# A hang in a peer or a held slot fails a test within this, not at the suite timeout.
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


def active_turn(session: AcpSession) -> CursorTurn:
    turn = cursor_adapter(session)._active_turn
    assert turn is not None, "no Cursor turn holds the provider slot"
    return turn


def turn_tools(session: AcpSession) -> TranscriptTools:
    """The tools the active turn was admitted with, where its notices land."""
    tools = active_turn(session).tools
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
    async with asyncio.timeout(WAIT_S):
        await provider.parked.wait()
        if provider.kind == "approval":
            await sending
    return sending


async def finish_turn(provider: BusyProvider, sending: asyncio.Task[Any]) -> None:
    """Let the peer end the occupying prompt and wait for the delivery to return,
    which frees the slot."""
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


def line_up(session: AcpSession, *rooms: str, generation: int = 1) -> None:
    """Register each room's capacity request, in order, as its scheduler would."""
    for room in rooms:
        cursor_adapter(session).register_capacity_wait(
            room_id=room, generation=generation
        )


def decision_token(session: AcpSession) -> str:
    """The token in the decision prompt the last delivery posted."""
    return prompt_token(session.last_reply.texts[-1])


def select_command(token: str, option: str = "allow-1") -> str:
    """The room command that picks ``option`` for the decision ``token``."""
    return f"{ROOM_COMMAND} {CursorCommandWord.SELECT} {token} {option}"


def deny_command(token: str) -> str:
    """The room command that denies the decision ``token``."""
    return f"{ROOM_COMMAND} {CursorCommandWord.DENY} {token}"


def hold_cancellation(session: AcpSession) -> Gate:
    """Hold the provider task inside its cancellation until ``release`` is set.

    The real ``session/cancel`` still goes out afterwards, so the turn is
    mid-teardown, not yet terminated, while the gate is closed.
    """
    adapter = cursor_adapter(session)
    gate = Gate()
    send_cancel = adapter._cancel_agent_turn

    async def held(runtime: ACPRuntime, *, room_id: str, session_id: str) -> None:
        gate.received.set()
        await gate.release.wait()
        await send_cancel(runtime, room_id=room_id, session_id=session_id)

    adapter._cancel_agent_turn = held  # type: ignore[method-assign]
    return gate


@contextmanager
def refusing_turn_tasks() -> Iterator[None]:
    """Make the loop refuse to create the Cursor provider task."""
    loop = asyncio.get_running_loop()
    previous = loop.get_task_factory()

    def factory(
        loop: asyncio.AbstractEventLoop, coro: Any, **kwargs: Any
    ) -> asyncio.Future[Any]:
        if coro.__name__ == "_run_turn":
            raise RuntimeError("the loop refuses new tasks")
        return asyncio.Task(coro, loop=loop, **kwargs)

    loop.set_task_factory(factory)
    try:
        yield
    finally:
        loop.set_task_factory(previous)
