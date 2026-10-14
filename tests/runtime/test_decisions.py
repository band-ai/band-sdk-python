"""Room-level flows through the shared chat-mediated decision registry.

Each test drives ``DecisionRegistry`` the way the adapters do -- an asker
waits on every ask, replies claim and resolve, and whoever removes an open
ask resolves it -- then checks the one outcome every asker ends up with.

Deadlines run on looptime's virtual clock, which jumps to the next timer only
once every task is idle, so each expiry lands exactly when it is due and every
other ordering is set by awaiting what the flow observably did.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest

from band.runtime.decisions import DecisionEntry, DecisionRegistry, Timeout
from tests.runtime.decisionroom import (
    DEADLINE_S,
    SHORT_DEADLINE_S,
    Ask,
    Expiries,
    Room,
)

pytestmark = pytest.mark.looptime


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


class TestDecisionRegistryRoomFlows:
    async def test_a_busy_room_gives_every_asker_exactly_one_outcome(
        self,
        open_room: Callable[..., Room],
    ) -> None:
        """At capacity with a reply mid-flight: the oldest open ask is evicted
        (never the claimed one), a redelivery supersedes its predecessor without
        evicting, a redelivery of the claimed ask is refused, the claimant's
        late answer still wins past the deadline, and the rest time out."""
        room = open_room(max_pending=2)
        await room.ask("a", key="a", timeout_s=SHORT_DEADLINE_S)
        await room.ask("b", key="b")
        claimed = room.registry.try_claim("a")
        assert claimed is not None

        await room.ask("c1", key="c")
        await room.ask("c2", key="c")
        assert await room.ask("a-again", key="a") is None
        assert list(room.registry) == ["a", "c"]

        # c2's deadline is due after a's, so once c2 has expired a is past its own.
        assert await room.askers["c2"] is Timeout.TIMED_OUT
        claimed.payload.future.set_result("accept")

        assert await room.outcomes() == {
            "a": "accept",
            "b": "evicted",
            "c1": "replaced",
            "c2": Timeout.TIMED_OUT,
        }
        assert not room.registry
        assert room.reply("c", "accept") is False

    async def test_tearing_down_a_room_resolves_its_open_asks(
        self,
        open_room: Callable[..., Room],
    ) -> None:
        """Teardown resolves the room's open asks and leaves a claimed one to its
        claimant; an asker cancelled with its turn drops its ask without
        cancelling the answer's future."""
        room = open_room()
        await room.ask("open")
        claimed_ask = await room.ask("claimed")
        abandoned = await room.ask("abandoned")
        assert claimed_ask is not None and abandoned is not None
        claimed = room.registry.try_claim(claimed_ask.token)
        assert claimed is not None
        await room.abandon("abandoned")

        room.tear_down()
        claimed.payload.future.set_result("accept")

        assert await room.outcomes() == {"open": "cancelled", "claimed": "accept"}
        assert not abandoned.payload.future.cancelled()
        assert not room.registry

    async def test_expiry_timers_race_replies_redeliveries_and_teardown(
        self,
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

        registry.start_timeout(ask("answered", "p1"), SHORT_DEADLINE_S, expiries)
        first = ask("first", "p2")
        registry.start_timeout(first, SHORT_DEADLINE_S, expiries)
        redelivery = ask("redelivery", "p2")
        registry.start_timeout(first, SHORT_DEADLINE_S, expiries)
        registry.start_timeout(redelivery, DEADLINE_S, expiries)
        registry.start_timeout(ask("slow", "p3"), 0, slow_expiry)
        assert registry.try_claim("p1") is not None

        await replying.wait()
        # Every other timer was due before the redelivery's; only it may fire.
        await expiries.until("redelivery")
        assert expiries.names == ["redelivery"]

        assert registry.cancel_all() == []
        release.set()
        await expiries.until("slow")
        assert expiries.names == ["redelivery", "slow"]

    async def test_stale_handles_never_act_on_a_newer_registration(self) -> None:
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

    async def test_a_failed_prompt_withdraws_the_ask_unless_a_reply_claimed_it(
        self,
    ) -> None:
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

        assert await registry.wait(
            answered, answered_ask.future, timeout_s=DEADLINE_S
        ) == ("accept")
        assert not registry

    async def test_a_turn_cancelled_as_its_ask_resolves_still_unwinds(
        self,
        open_room: Callable[..., Room],
    ) -> None:
        """Room cleanup resolves an open ask, then cancels the turn parked on it
        in the same tick. The turn must still end cancelled: an answer that
        swallows the cancel lets it carry on into a connection already closing,
        and the shutdown awaiting it never returns."""
        room = open_room()
        await room.ask("parked")
        asker = room.askers.pop("parked")

        room.tear_down()
        asker.cancel()
        await asyncio.wait([asker])

        assert asker.cancelled()
