"""Tests for the shared chat-mediated decision registry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from band.runtime.decisions import DecisionEntry, DecisionRegistry, Timeout


@dataclass
class Ask:
    name: str
    future: asyncio.Future[str] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )


class TimeoutRecorder:
    def __init__(self) -> None:
        self.expired: list[str] = []

    async def __call__(self, entry: DecisionEntry[Ask]) -> None:
        self.expired.append(entry.payload.name)


def unclaimed_names(registry: DecisionRegistry[Ask]) -> list[str]:
    return [entry.payload.name for entry in registry.unclaimed()]


class TestMapping:
    async def test_behaves_as_a_read_only_mapping_of_token_to_payload(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        a, b = Ask("a"), Ask("b")
        registry.register(a, key="t-a")
        registry.register(b, key="t-b")

        assert dict(registry) == {"t-a": a, "t-b": b}
        assert "t-a" in registry
        assert registry.get("missing") is None

    async def test_claimed_entries_stay_visible_until_forgotten(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        entry = registry.register(Ask("a"))
        registry.try_claim(entry.token)
        assert entry.token in registry

        registry.forget(entry)
        assert not registry

    async def test_follows_core_removals_and_keeps_registration_order(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        entries = {name: registry.register(Ask(name), key=name) for name in "abc"}
        registry.register(Ask("a-again"), key="a")

        assert registry.withdraw(entries["b"]) is True
        assert [(token, ask.name) for token, ask in registry.items()] == [
            ("a", "a-again"),
            ("c", "c"),
        ]


class TestRegister:
    async def test_mints_a_distinct_token_per_unkeyed_registration(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        first = registry.register(Ask("a"))
        second = registry.register(Ask("b"))
        assert first.token != second.token

    async def test_redelivered_key_replaces_the_unclaimed_entry_and_its_timer(
        self,
    ) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        recorder = TimeoutRecorder()
        first = registry.register(Ask("first"), key="req-1")
        assert first is not None
        registry.start_timeout(first, 0.01, recorder)

        second = registry.register(Ask("second"), key="req-1")
        assert second is not None
        registry.start_timeout(first, 0.01, recorder)
        registry.start_timeout(second, 0.02, recorder)
        await asyncio.sleep(0.05)

        assert recorder.expired == ["second"]

    async def test_redelivered_key_never_displaces_a_claimed_entry(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        registry.register(Ask("first"), key="req-1")
        registry.try_claim("req-1")

        assert registry.register(Ask("second"), key="req-1") is None
        assert registry["req-1"].name == "first"


class TestClaim:
    async def test_only_the_first_claim_wins(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        entry = registry.register(Ask("a"))

        assert [
            registry.try_claim(entry.token),
            registry.try_claim(entry.token),
        ] == [entry, None]

    async def test_a_claim_before_the_deadline_stops_the_timeout(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        recorder = TimeoutRecorder()
        entry = registry.register(Ask("a"))
        registry.start_timeout(entry, 0.01, recorder)

        registry.try_claim(entry.token)
        await asyncio.sleep(0.03)

        assert recorder.expired == []

    async def test_an_expired_timeout_owns_the_entry_so_a_late_reply_loses(
        self,
    ) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        recorder = TimeoutRecorder()
        entry = registry.register(Ask("a"))
        registry.start_timeout(entry, 0.01, recorder)
        await asyncio.sleep(0.03)

        assert recorder.expired == ["a"]
        assert registry.try_claim(entry.token) is None


class TestWithdraw:
    async def test_drops_an_ask_nobody_has_claimed(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        entry = registry.register(Ask("a"))

        assert registry.withdraw(entry) is True
        assert entry.token not in registry

    async def test_leaves_a_claimed_ask_to_its_claimant(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        entry = registry.register(Ask("a"))
        registry.try_claim(entry.token)

        assert registry.withdraw(entry) is False
        assert entry.token in registry


class TestSupersededRegistration:
    """A keyed redelivery replaces an unclaimed ask under a new ticket, so
    whoever still holds the old entry can no longer act on the new one."""

    async def test_a_late_forget_or_withdraw_leaves_the_redelivery_intact(
        self,
    ) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        first = registry.register(Ask("first"), key="req-1")
        assert first is not None
        registry.register(Ask("second"), key="req-1")

        registry.forget(first)

        assert registry.withdraw(first) is False
        assert unclaimed_names(registry) == ["second"]

    async def test_the_replaced_waiter_times_out_while_the_redelivery_is_answered(
        self,
    ) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        first_ask, second_ask = Ask("first"), Ask("second")
        first = registry.register(first_ask, key="req-1")
        assert first is not None
        first_waiter = asyncio.create_task(
            registry.wait(first, first_ask.future, timeout_s=0.01)
        )
        await asyncio.sleep(0)

        second = registry.register(second_ask, key="req-1")
        assert second is not None
        second_waiter = asyncio.create_task(
            registry.wait(second, second_ask.future, timeout_s=1.0)
        )

        assert await first_waiter is Timeout.TIMED_OUT
        assert registry.try_claim("req-1") is second
        second_ask.future.set_result("accept")
        assert await second_waiter == "accept"
        assert not registry


class TestEviction:
    async def test_unbounded_registry_never_evicts(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        for index in range(50):
            registry.register(Ask(str(index)))
        assert registry.evict_oldest() is None

    async def test_below_capacity_does_not_evict(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry(max_pending=2)
        registry.register(Ask("a"))
        assert registry.evict_oldest() is None

    async def test_at_capacity_evicts_the_oldest_unclaimed_and_stops_its_timer(
        self,
    ) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry(max_pending=3)
        recorder = TimeoutRecorder()
        entries = {
            name: registry.register(Ask(name), key=name)
            for name in ("claimed", "oldest", "newest")
        }
        oldest = entries["oldest"]
        assert oldest is not None
        registry.start_timeout(oldest, 0.01, recorder)
        registry.try_claim("claimed")

        evicted = registry.evict_oldest()
        await asyncio.sleep(0.03)

        assert evicted is not None and evicted.payload.name == "oldest"
        assert list(registry) == ["claimed", "newest"]
        assert recorder.expired == []

    async def test_at_capacity_with_every_entry_claimed_evicts_nothing(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry(max_pending=2)
        for name in ("a", "b"):
            registry.register(Ask(name), key=name)
            registry.try_claim(name)

        assert registry.evict_oldest() is None
        assert list(registry) == ["a", "b"]


class TestCancel:
    async def test_cancel_room_drops_its_asks_but_hands_back_only_unclaimed_ones(
        self,
    ) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        open_ask = registry.register(Ask("open"), room_id="room-1")
        claimed = registry.register(Ask("claimed"), room_id="room-1")
        other_room = registry.register(Ask("other-room"), room_id="room-2")
        registry.try_claim(claimed.token)

        to_resolve = registry.cancel_room("room-1")

        assert to_resolve == [open_ask]
        assert list(registry) == [other_room.token]
        assert [
            entry.payload.name for entry in registry.unclaimed_in_room("room-2")
        ] == ["other-room"]

    async def test_stops_pending_timers(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        recorder = TimeoutRecorder()
        entry = registry.register(Ask("a"))
        registry.start_timeout(entry, 0.01, recorder)

        registry.cancel_all()
        await asyncio.sleep(0.03)

        assert recorder.expired == []

    async def test_never_interrupts_an_expiry_that_already_claimed_its_ask(
        self,
    ) -> None:
        """The expiry owns an ask it claimed; cancelling everything mid
        on_timeout (e.g. room teardown during the timeout's reply) must let
        that reply finish."""
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        replying, release = asyncio.Event(), asyncio.Event()
        replied: list[str] = []

        async def reply_on_timeout(entry: DecisionEntry[Ask]) -> None:
            replying.set()
            await release.wait()
            replied.append(entry.payload.name)

        entry = registry.register(Ask("a"))
        registry.start_timeout(entry, 0, reply_on_timeout)
        await replying.wait()

        assert registry.cancel_all() == []
        release.set()
        await asyncio.sleep(0)

        assert replied == ["a"]


class TestWait:
    async def test_returns_the_reply_that_claimed_it(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        ask = Ask("a")
        entry = registry.register(ask)

        async def reply() -> None:
            await asyncio.sleep(0.01)
            assert registry.try_claim(entry.token) is not None
            ask.future.set_result("accept")

        replier = asyncio.create_task(reply())
        assert await registry.wait(entry, ask.future, timeout_s=1.0) == "accept"
        await replier
        assert entry.token not in registry

    async def test_times_out_when_nobody_replies_and_rejects_late_replies(
        self,
    ) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        ask = Ask("a")
        entry = registry.register(ask)

        assert await registry.wait(entry, ask.future, timeout_s=0.01) is (
            Timeout.TIMED_OUT
        )
        assert entry.token not in registry
        assert registry.try_claim(entry.token) is None

    async def test_a_reply_that_claimed_before_the_deadline_wins_even_if_it_resolves_after(
        self,
    ) -> None:
        """A reply handler claims, then awaits a slow room notice before
        resolving; the deadline passing meanwhile must not override it."""
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        ask = Ask("a")
        entry = registry.register(ask)
        notice_sent = asyncio.Event()

        async def slow_reply() -> None:
            registry.try_claim(entry.token)
            await notice_sent.wait()
            ask.future.set_result("accept")

        replier = asyncio.create_task(slow_reply())
        waiter = asyncio.create_task(registry.wait(entry, ask.future, timeout_s=0.01))
        await asyncio.sleep(0.03)
        assert not waiter.done()

        notice_sent.set()
        assert await waiter == "accept"
        await replier

    async def test_evicting_a_waiter_resolves_it_with_the_forced_value(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry(max_pending=1)
        first = Ask("first")
        first_entry = registry.register(first)
        first_waiter = asyncio.create_task(
            registry.wait(first_entry, first.future, timeout_s=1.0)
        )
        await asyncio.sleep(0)

        evicted = registry.evict_oldest()
        assert evicted is not None
        evicted.payload.future.set_result("decline")

        assert await first_waiter == "decline"

    async def test_a_cancelled_waiter_forgets_its_token_without_cancelling_the_future(
        self,
    ) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        ask = Ask("a")
        entry = registry.register(ask)
        waiter = asyncio.create_task(registry.wait(entry, ask.future, timeout_s=1.0))
        await asyncio.sleep(0)

        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert entry.token not in registry
        assert not ask.future.cancelled()

    async def test_overlapping_asks_each_resolve_exactly_once(self) -> None:
        """A room at capacity: asks keep arriving (evicting the oldest open
        one), some get a reply, the rest time out -- every asker gets exactly
        one outcome and the registry drains."""
        registry: DecisionRegistry[Ask] = DecisionRegistry(max_pending=2)
        waiters: dict[str, asyncio.Task[str | Timeout]] = {}
        asks: dict[str, Ask] = {}

        for name in ("a", "b", "c", "d"):
            if (evicted := registry.evict_oldest()) is not None:
                evicted.payload.future.set_result("evicted")
            asks[name] = ask = Ask(name)
            entry = registry.register(ask, key=name)
            assert entry is not None
            waiters[name] = asyncio.create_task(
                registry.wait(entry, ask.future, timeout_s=0.05)
            )
            await asyncio.sleep(0)

        assert registry.try_claim("d") is not None
        asks["d"].future.set_result("accept")

        outcomes = {name: await waiter for name, waiter in waiters.items()}
        assert outcomes == {
            "a": "evicted",
            "b": "evicted",
            "c": Timeout.TIMED_OUT,
            "d": "accept",
        }
        assert not registry
