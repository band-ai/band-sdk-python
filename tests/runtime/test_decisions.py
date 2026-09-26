"""Room-level flows through the shared chat-mediated decision registry.

Each test drives ``DecisionRegistry`` the way the adapters do -- an asker
waits on every ask, replies claim and resolve, and whoever removes an open
ask resolves it -- then checks the one outcome every asker ends up with.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import pytest

from band.runtime.decisions import DecisionEntry, DecisionRegistry, Timeout

Outcome = str | Timeout

#: Short enough to keep the suite fast, long enough to order events around it.
DEADLINE_S = 0.02
PAST_DEADLINE_S = 3 * DEADLINE_S


@dataclass
class Ask:
    name: str
    future: asyncio.Future[str] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )


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
        room_id: str | None = None,
        timeout_s: float = 1.0,
    ) -> DecisionEntry[Ask] | None:
        ask = Ask(name)
        registration = (
            self.registry.register_minted(ask, room_id=room_id)
            if key is None
            else self.registry.register_keyed(ask, key=key)
        )
        if registration is None:
            return None
        for removed in registration.removed:
            why = "evicted" if removed is registration.evicted else "replaced"
            removed.payload.future.set_result(why)
        self.askers[name] = asyncio.create_task(
            self.registry.wait(registration.entry, ask.future, timeout_s=timeout_s)
        )
        await asyncio.sleep(0)
        return registration.entry

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

    def tear_down(self, room_id: str) -> None:
        for entry in self.registry.cancel_room(room_id):
            entry.payload.future.set_result("cancelled")

    async def outcomes(self) -> dict[str, Outcome]:
        return {name: await asker for name, asker in self.askers.items()}


class Expiries:
    """An ``on_timeout`` callback that records which asks expired."""

    def __init__(self) -> None:
        self.names: list[str] = []

    async def __call__(self, entry: DecisionEntry[Ask]) -> None:
        self.names.append(entry.payload.name)


@pytest.fixture
async def open_room() -> AsyncIterator[Callable[..., Room]]:
    rooms: list[Room] = []

    def open_(*, max_pending: int | None = None) -> Room:
        rooms.append(room := Room(DecisionRegistry(max_pending=max_pending)))
        return room

    yield open_
    for room in rooms:
        for asker in room.askers.values():
            asker.cancel()


@pytest.fixture
def expiries() -> Expiries:
    return Expiries()


async def test_a_busy_room_gives_every_asker_exactly_one_outcome(
    open_room: Callable[..., Room],
) -> None:
    """At capacity with a reply mid-flight: the oldest open ask is evicted
    (never the claimed one), a redelivery supersedes its predecessor without
    evicting, a redelivery of the claimed ask is refused, the claimant's
    late answer still wins past the deadline, and the rest time out."""
    room = open_room(max_pending=2)
    await room.ask("a", key="a", timeout_s=DEADLINE_S)
    await room.ask("b", key="b")
    claimed = room.registry.try_claim("a")
    assert claimed is not None

    await room.ask("c1", key="c", timeout_s=DEADLINE_S)
    await room.ask("c2", key="c", timeout_s=DEADLINE_S)
    assert await room.ask("a-again", key="a") is None
    assert list(room.registry) == ["a", "c"]

    await asyncio.sleep(PAST_DEADLINE_S)
    claimed.payload.future.set_result("accept")

    assert await room.outcomes() == {
        "a": "accept",
        "b": "evicted",
        "c1": "replaced",
        "c2": Timeout.TIMED_OUT,
    }
    assert not room.registry
    assert room.reply("c", "accept") is False


async def test_tearing_down_a_room_resolves_only_its_open_asks(
    open_room: Callable[..., Room],
) -> None:
    """Teardown resolves the room's open asks, leaves a claimed one to its
    claimant, and never touches another room; an asker cancelled with its
    turn drops its ask without cancelling the answer's future."""
    room = open_room()
    await room.ask("open", room_id="room-1")
    claimed_ask = await room.ask("claimed", room_id="room-1")
    await room.ask("elsewhere", room_id="room-2")
    abandoned = await room.ask("abandoned", room_id="room-2")
    assert claimed_ask is not None and abandoned is not None
    claimed = room.registry.try_claim(claimed_ask.token)
    assert claimed is not None

    room.tear_down("room-1")
    claimed.payload.future.set_result("accept")
    await room.abandon("abandoned")

    [survivor] = room.registry.unclaimed_in_room("room-2")
    assert survivor.payload.name == "elsewhere"
    assert room.reply(survivor.token, "decline")
    assert await room.outcomes() == {
        "open": "cancelled",
        "claimed": "accept",
        "elsewhere": "decline",
    }
    assert not abandoned.payload.future.cancelled()
    assert not room.registry


async def test_expiry_timers_race_replies_redeliveries_and_teardown(
    expiries: Expiries,
) -> None:
    """A reply before the deadline stops that ask's timer; a redelivery drops
    its predecessor's timer and a stale handle can't arm a new one; and
    teardown during an expiry that already claimed its ask lets it finish."""
    registry: DecisionRegistry[Ask] = DecisionRegistry()
    replying, release = asyncio.Event(), asyncio.Event()

    async def slow_expiry(entry: DecisionEntry[Ask]) -> None:
        replying.set()
        await release.wait()
        await expiries(entry)

    def ask(name: str, key: str) -> DecisionEntry[Ask]:
        registration = registry.register_keyed(Ask(name), key=key)
        assert registration is not None
        return registration.entry

    registry.start_timeout(ask("answered", "p1"), DEADLINE_S, expiries)
    first = ask("first", "p2")
    registry.start_timeout(first, DEADLINE_S, expiries)
    redelivery = ask("redelivery", "p2")
    registry.start_timeout(first, DEADLINE_S, expiries)
    registry.start_timeout(redelivery, DEADLINE_S, expiries)
    registry.start_timeout(ask("slow", "p3"), 0, slow_expiry)
    assert registry.try_claim("p1") is not None

    await replying.wait()
    await asyncio.sleep(PAST_DEADLINE_S)
    assert expiries.names == ["redelivery"]

    assert registry.cancel_all() == []
    release.set()
    await asyncio.sleep(0)
    assert expiries.names == ["redelivery", "slow"]


async def test_stale_handles_never_act_on_a_newer_registration() -> None:
    """A handle outlives its registration -- replaced by a redelivery, or
    issued by a room registry teardown has since replaced -- and every
    operation through it must leave the newer registration alone."""
    registry: DecisionRegistry[Ask] = DecisionRegistry()
    old_ask = Ask("old")
    old = registry.register_keyed(old_ask, key="k")
    assert old is not None
    replacement = registry.register_keyed(Ask("new"), key="k")
    assert replacement is not None and replacement.replaced is old.entry

    registry.forget(old.entry)
    assert registry.withdraw(old.entry) is False
    registry.start_timeout(old.entry, 0, Expiries())
    assert await registry.wait(old.entry, old_ask.future, timeout_s=DEADLINE_S) is (
        Timeout.TIMED_OUT
    )

    fresh: DecisionRegistry[Ask] = DecisionRegistry()
    assert fresh.register_keyed(Ask("fresh"), key="k") is not None
    fresh.forget(old.entry)
    assert fresh.withdraw(old.entry) is False

    assert [entry.payload.name for entry in registry.unclaimed()] == ["new"]
    assert [entry.payload.name for entry in fresh.unclaimed()] == ["fresh"]


async def test_a_failed_prompt_withdraws_the_ask_unless_a_reply_claimed_it() -> None:
    """The prompt send failed: an unanswered ask is withdrawn so nobody waits
    on it, but a reply that already claimed one owns its answer."""
    registry: DecisionRegistry[Ask] = DecisionRegistry()
    unanswered = registry.register_minted(Ask("unanswered")).entry
    answered_ask = Ask("answered")
    answered = registry.register_minted(answered_ask).entry
    claimed = registry.try_claim(answered.token)
    assert claimed is not None

    assert registry.withdraw(unanswered) is True
    assert registry.withdraw(answered) is False
    claimed.payload.future.set_result("accept")

    assert await registry.wait(answered, answered_ask.future, timeout_s=1.0) == (
        "accept"
    )
    assert not registry
