"""The shape every backend-stop probe shares.

A probe starts a turn through an adapter's real ``on_event``, against a fake
backend that records each cancel it receives, and ends that turn as the
runtime would. ``test_backend_stop`` asserts the cancels each way of ending a
turn must leave behind.

Imports gated behind an optional extra stay inside the probe that needs them:
not every lane's venv has every framework installed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum

from band.client.streaming import ControlMode
from band.core.simple_adapter import SimpleAdapter

#: How long a probe waits for its turn to reach the backend, and how often it
#: looks.
STARTUP_TIMEOUT_SECONDS = 5.0
STARTUP_POLL_SECONDS = 0.001

#: The controls that end a running turn.
ENDING_CONTROLS = [ControlMode.STOP, ControlMode.INTERRUPT]


class NoBackendTurn(StrEnum):
    """Why an adapter has no backend turn to stop when its turn ends early."""

    IN_PROCESS = "runs its model in this process; cancelling the handler stops it"
    GATEWAY = "serves Band peers to outside callers; it drives no backend turn"
    CHANNEL = "relays a chat channel; it drives no backend turn"


@dataclass(frozen=True)
class StopCase:
    """One way a room's turn ends, and the backend cancels it must record."""

    run: Callable[[], Awaitable[list[str]]]
    expected: list[str]


async def wait_until(condition: Callable[[], bool]) -> None:
    """Let the turn run until ``condition`` holds."""
    async with asyncio.timeout(STARTUP_TIMEOUT_SECONDS):
        while not condition():
            await asyncio.sleep(STARTUP_POLL_SECONDS)


async def end_turn(
    adapter: SimpleAdapter, turn: asyncio.Task[None], room_id: str, mode: ControlMode
) -> None:
    """End ``turn`` as the runtime does, then let its backend stop finish."""
    turn.cancel()
    with suppress(asyncio.CancelledError):
        await turn
    await adapter.on_interrupt(room_id, mode)
    assert adapter.backend_turns is not None
    await adapter.backend_turns.drain()


async def stop_idle_room(adapter: SimpleAdapter, room_id: str) -> None:
    """A STOP for a room with no turn in progress."""
    await adapter.on_interrupt(room_id, ControlMode.STOP)
    assert adapter.backend_turns is not None
    await adapter.backend_turns.drain()
