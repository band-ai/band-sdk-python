"""The agent's side of a scenario: turns, controls and room cleanups on cue,
each written to the shared journal."""

from __future__ import annotations

import asyncio

from band.client.streaming import ControlMode
from band.platform.event import MessageEvent, PlatformEvent
from band.runtime.execution import ExecutionContext
from tests.scenarios.observations import Observations


class Script:
    """Runs each turn and room cleanup, holding or failing them on cue.

    Turns are keyed by message text, so a test names the message it holds.
    """

    def __init__(self, observations: Observations) -> None:
        self.observations = observations
        self.failing_cleanups: set[str] = set()
        self._held_turns: dict[str, asyncio.Event] = {}
        self._cleanup_release = asyncio.Event()
        self._cleanup_release.set()

    def hold_turn(self, text: str) -> None:
        self._held_turns[text] = asyncio.Event()

    def release_turn(self, text: str) -> None:
        self._held_turns.pop(text).set()

    def hold_cleanups(self) -> None:
        self._cleanup_release.clear()

    def release_cleanups(self) -> None:
        self._cleanup_release.set()

    def release_everything(self) -> None:
        for held in self._held_turns.values():
            held.set()
        self._held_turns.clear()
        self.failing_cleanups.clear()
        self._cleanup_release.set()

    async def on_execute(self, ctx: ExecutionContext, event: PlatformEvent) -> None:
        if not isinstance(event, MessageEvent) or event.payload is None:
            return
        text = event.payload.content
        self.observations.record(f"{ctx.room_id}: turn {text}")
        if (held := self._held_turns.get(text)) is not None:
            try:
                await held.wait()
            except asyncio.CancelledError:
                self.observations.record(f"{ctx.room_id}: turn {text} cancelled")
                raise

    async def on_control(self, room_id: str, mode: ControlMode) -> None:
        """The adapter's own control hook, as the runtime notifies it."""
        self.observations.record(f"{room_id}: control {mode.value}")

    async def cleanup(self, room_id: str) -> None:
        self.observations.record(f"{room_id}: cleanup started")
        await self._cleanup_release.wait()
        if room_id in self.failing_cleanups:
            self.observations.record(f"{room_id}: cleanup failed")
            raise RuntimeError(f"cleanup of {room_id} failed")
        self.observations.record(f"{room_id}: cleanup done")


class BareExecution:
    """A custom execution with none of the optional control methods."""

    def __init__(self, room_id: str, observations: Observations) -> None:
        self.room_id = room_id
        self.observations = observations

    async def start(self) -> None:
        self.observations.record(f"{self.room_id}: bare start")

    async def stop(self, timeout: float | None = None) -> bool:
        self.observations.record(f"{self.room_id}: bare stop")
        return True

    async def on_event(self, event: PlatformEvent) -> None:
        if isinstance(event, MessageEvent) and event.payload is not None:
            self.observations.record(
                f"{self.room_id}: bare saw {event.payload.content}"
            )
