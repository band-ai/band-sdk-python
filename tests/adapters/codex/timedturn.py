"""A Codex turn run beside its test on the virtual clock, and the failure its
total limit reports."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from band.adapters.codex import CodexAdapter
from band.core.protocols import FAILURE_CODE_TIMEOUT
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.codex.kit import ROOM_ID, send_bootstrap
from tests.adapters.codexturns import FIRST_THREAD_ID

# How far from a limit the virtual clock is read, on either side of it.
MARGIN_S = 1.0
#: The id of the first turn a FakeCodexClient starts.
FIRST_TURN_ID = "turn-1"
INTERRUPTED_FIRST_TURN = [{"threadId": FIRST_THREAD_ID, "turnId": FIRST_TURN_ID}]


def failures(tools: FakeAgentTools) -> list[tuple[str, str]]:
    """Every failure a delivery reported, as ``(code, message)``."""
    return [
        (failure["code"], failure["message"]) for failure in reported_failures(tools)
    ]


def past_its_total(seconds: float) -> list[tuple[str, str]]:
    """The one failure of a turn stopped by its total limit of ``seconds``."""
    return [(FAILURE_CODE_TIMEOUT, f"Codex turn timed out after {seconds}s")]


@asynccontextmanager
async def running_turn(
    adapter: CodexAdapter, tools: FakeAgentTools, room_id: str = ROOM_ID
) -> AsyncIterator[asyncio.Task[None]]:
    """Deliver ``room_id`` a message and run its turn beside the test; the turn
    never outlives the block."""
    turn = asyncio.create_task(send_bootstrap(adapter, room_id=room_id, tools=tools))
    try:
        yield turn
    finally:
        turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
