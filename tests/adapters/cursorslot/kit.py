"""Doubles and helpers for the Cursor provider-slot tests."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from acp.schema import AllowedOutcome, ToolCallUpdate

from band.adapters.cursor_acp import CursorACPAdapter, CursorTurn
from band.integrations.acp.client_runtime import ACPRuntime
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent, Gate, allow_or_reject
from tests.integrations.acp.acp_toolkit.harness import AcpSession

ROOM_A = "room-A"
ROOM_B = "room-B"

BusyKind = Literal["prompt", "approval"]


@dataclass
class BusyProvider:
    """A peer whose chosen prompts (1-based) stay in flight; the rest return at once.

    ``prompt`` parks inside the prompt itself. ``approval`` parks on a Cursor
    permission request, so the delivery worker is released while the provider
    keeps running.
    """

    agent: FakeACPAgent
    kind: BusyKind
    parked: asyncio.Event


def busy_provider(
    kind: BusyKind, *, on_prompts: Collection[int] = (1,)
) -> BusyProvider:
    parked = asyncio.Event()
    agent = FakeACPAgent()

    @agent.on_prompt
    async def run(peer: FakeACPAgent, session_id: str) -> None:
        if len(peer.prompts) not in on_prompts:
            return
        parked.set()
        match kind:
            case "prompt":
                await asyncio.Event().wait()
            case "approval":
                response = await peer.ask_permission(
                    session_id,
                    ToolCallUpdate(tool_call_id="tc-1", title="shell"),
                    allow_or_reject(),
                )
                peer.approved = isinstance(response.outcome, AllowedOutcome)

    return BusyProvider(agent=agent, kind=kind, parked=parked)


def cursor_adapter(session: AcpSession) -> CursorACPAdapter:
    assert isinstance(session.adapter, CursorACPAdapter)
    return session.adapter


def active_turn(session: AcpSession) -> CursorTurn:
    turn = cursor_adapter(session)._active_turn
    assert turn is not None, "no Cursor turn holds the provider slot"
    return turn


async def occupy(
    session: AcpSession, provider: BusyProvider, *, room: str = ROOM_A
) -> asyncio.Task[Any]:
    """Start a turn in ``room`` and return once the peer holds its prompt.

    The returned task is the delivery worker's ``on_message`` call: it ends
    with the turn for ``prompt`` and as soon as the decision opens for
    ``approval``.
    """
    provider.parked.clear()
    sending = asyncio.create_task(session.send("go", room=room))
    await provider.parked.wait()
    if provider.kind == "approval":
        await sending
    return sending


def decision_token(session: AcpSession) -> str:
    """The token in the decision prompt the last delivery posted."""
    match = re.search(r"`/cursor \w+ (\S+)", session.last_reply.texts[-1])
    assert match is not None
    return match[1]


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
