"""An adapter's side of a ``DecisionRegistry``, for room-level flow tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from band.runtime.decisions import DecisionEntry, DecisionRegistry, Timeout

Outcome = str | Timeout

#: Virtual seconds; distinct deadlines fix which of two expiries is due first.
SHORT_DEADLINE_S = 30.0
DEADLINE_S = 60.0


@dataclass
class Ask:
    name: str
    future: asyncio.Future[str] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )
    waiting: asyncio.Event = field(default_factory=asyncio.Event)


class Room:
    """An adapter's side of the registry: each ask gets a waiting asker, and
    every open ask the registry hands back is resolved with why it went."""

    def __init__(self, registry: DecisionRegistry[Ask]) -> None:
        self.registry = registry
        self.askers: dict[str, asyncio.Task[Outcome]] = {}

    async def ask(
        self,
        name: str,
        *,
        key: str | None = None,
        timeout_s: float = DEADLINE_S,
    ) -> DecisionEntry[Ask] | None:
        ask = Ask(name)
        registration = (
            self.registry.register_minted(ask)
            if key is None
            else self.registry.register_keyed(ask, key=key)
        )
        if registration is None:
            return None
        for removed in registration.removed:
            why = "evicted" if removed is registration.evicted else "replaced"
            removed.payload.future.set_result(why)
        self.askers[name] = asyncio.create_task(
            self._wait(registration.entry, ask, timeout_s)
        )
        await ask.waiting.wait()
        return registration.entry

    async def _wait(
        self, entry: DecisionEntry[Ask], ask: Ask, timeout_s: float
    ) -> Outcome:
        # wait() runs straight into its first suspension once called, so a
        # caller woken by ``waiting`` finds the asker already parked in it.
        ask.waiting.set()
        return await self.registry.wait(entry, ask.future, timeout_s=timeout_s)

    def reply(self, token: str, answer: str) -> bool:
        """A room reply: claim, then resolve with no await in between."""
        if (entry := self.registry.try_claim(token)) is None:
            return False
        entry.payload.future.set_result(answer)
        return True

    async def abandon(self, name: str) -> None:
        """The asker's turn is cancelled; wait until it has fully unwound."""
        asker = self.askers.pop(name)
        asker.cancel()
        await asyncio.wait([asker])

    def tear_down(self) -> None:
        for entry in self.registry.cancel_all():
            entry.payload.future.set_result("cancelled")

    async def outcomes(self) -> dict[str, Outcome]:
        return {name: await asker for name, asker in self.askers.items()}


class Expiries:
    """An ``on_timeout`` callback that records which asks expired."""

    def __init__(self) -> None:
        self.names: list[str] = []
        self._expired = asyncio.Condition()

    async def __call__(self, entry: DecisionEntry[Ask]) -> None:
        async with self._expired:
            self.names.append(entry.payload.name)
            self._expired.notify_all()

    async def until(self, name: str) -> None:
        async with self._expired:
            await self._expired.wait_for(lambda: name in self.names)
