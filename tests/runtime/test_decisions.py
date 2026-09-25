"""Tests for the shared chat-mediated decision registry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from band.runtime.decisions import ClaimOutcome, DecisionRegistry, Timeout


@dataclass
class Ask:
    name: str
    future: asyncio.Future[str] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )


class TimeoutRecorder:
    def __init__(self) -> None:
        self.expired: list[str] = []

    async def __call__(self, ask: Ask) -> None:
        self.expired.append(ask.name)


def unclaimed_names(registry: DecisionRegistry[Ask]) -> list[str]:
    return [entry.payload.name for entry in registry.entries() if not entry.claimed]


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
        token = registry.register(Ask("a"))
        registry.try_claim(token)
        assert token in registry

        registry.forget(token)
        assert not registry


class TestRegister:
    async def test_mints_a_distinct_token_per_unkeyed_registration(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        assert registry.register(Ask("a")) != registry.register(Ask("b"))

    async def test_redelivered_key_replaces_the_unclaimed_entry_and_its_timer(
        self,
    ) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        recorder = TimeoutRecorder()
        registry.register(Ask("first"), key="req-1")
        registry.start_timeout("req-1", 0.01, recorder)

        registry.register(Ask("second"), key="req-1")
        await asyncio.sleep(0.03)

        assert unclaimed_names(registry) == ["second"]
        assert recorder.expired == []

    async def test_redelivered_key_never_displaces_a_claimed_entry(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        registry.register(Ask("first"), key="req-1")
        registry.try_claim("req-1")

        assert registry.register(Ask("second"), key="req-1") is None
        assert registry["req-1"].name == "first"


class TestClaim:
    async def test_only_the_first_claim_wins(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        token = registry.register(Ask("a"))

        assert [registry.try_claim(token), registry.try_claim(token)] == [
            registry[token],
            None,
        ]

    async def test_a_claim_before_the_deadline_stops_the_timeout(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        recorder = TimeoutRecorder()
        token = registry.register(Ask("a"))
        registry.start_timeout(token, 0.01, recorder)

        registry.try_claim(token)
        await asyncio.sleep(0.03)

        assert recorder.expired == []

    async def test_an_expired_timeout_owns_the_entry_so_a_late_reply_loses(
        self,
    ) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        recorder = TimeoutRecorder()
        token = registry.register(Ask("a"))
        registry.start_timeout(token, 0.01, recorder)
        await asyncio.sleep(0.03)

        assert recorder.expired == ["a"]
        assert registry.claim_reply(token, "alice") is ClaimOutcome.NOT_PENDING


class TestClaimReply:
    @pytest.mark.parametrize(
        ("authorized_senders", "sender_id", "outcome"),
        [
            (None, None, ClaimOutcome.CLAIMED),
            (None, "anyone", ClaimOutcome.CLAIMED),
            ({"alice"}, "alice", ClaimOutcome.CLAIMED),
            ({"alice"}, "mallory", ClaimOutcome.UNAUTHORIZED),
            ({"alice"}, None, ClaimOutcome.UNAUTHORIZED),
            (set(), "alice", ClaimOutcome.UNAUTHORIZED),
        ],
    )
    async def test_gates_on_authorized_senders(
        self,
        authorized_senders: set[str] | None,
        sender_id: str | None,
        outcome: ClaimOutcome,
    ) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry(
            authorized_senders=authorized_senders
        )
        token = registry.register(Ask("a"))
        assert registry.claim_reply(token, sender_id) is outcome

    async def test_an_unauthorized_reply_leaves_the_decision_open(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry(authorized_senders={"alice"})
        token = registry.register(Ask("a"))

        assert registry.claim_reply(token, "mallory") is ClaimOutcome.UNAUTHORIZED
        assert registry.claim_reply(token, "alice") is ClaimOutcome.CLAIMED

    async def test_unknown_token_is_not_pending(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        assert registry.claim_reply("nope", None) is ClaimOutcome.NOT_PENDING


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
        for name in ("claimed", "oldest", "newest"):
            registry.register(Ask(name), key=name)
        registry.start_timeout("oldest", 0.01, recorder)
        registry.try_claim("claimed")

        evicted = registry.evict_oldest()
        await asyncio.sleep(0.03)

        assert evicted is not None and evicted.payload.name == "oldest"
        assert list(registry) == ["claimed", "newest"]
        assert recorder.expired == []


class TestCancelAll:
    async def test_drops_every_match_but_hands_back_only_unclaimed_ones(
        self,
    ) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        for name in ("open", "claimed", "other-room"):
            registry.register(Ask(name), key=name)
        registry.try_claim("claimed")

        to_resolve = registry.cancel_all(lambda ask: ask.name != "other-room")

        assert [entry.token for entry in to_resolve] == ["open"]
        assert list(registry) == ["other-room"]

    async def test_stops_pending_timers(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        recorder = TimeoutRecorder()
        token = registry.register(Ask("a"))
        registry.start_timeout(token, 0.01, recorder)

        registry.cancel_all()
        await asyncio.sleep(0.03)

        assert recorder.expired == []


class TestWait:
    async def test_returns_the_reply_that_claimed_it(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        ask = Ask("a")
        token = registry.register(ask)

        async def reply() -> None:
            await asyncio.sleep(0.01)
            assert registry.claim_reply(token, None) is ClaimOutcome.CLAIMED
            ask.future.set_result("accept")

        replier = asyncio.create_task(reply())
        assert await registry.wait(token, ask.future, timeout_s=1.0) == "accept"
        await replier
        assert token not in registry

    async def test_times_out_when_nobody_replies_and_rejects_late_replies(
        self,
    ) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        ask = Ask("a")
        token = registry.register(ask)

        assert await registry.wait(token, ask.future, timeout_s=0.01) is (
            Timeout.TIMED_OUT
        )
        assert token not in registry
        assert registry.claim_reply(token, None) is ClaimOutcome.NOT_PENDING

    async def test_a_reply_that_claimed_before_the_deadline_wins_even_if_it_resolves_after(
        self,
    ) -> None:
        """A reply handler claims, then awaits a slow room notice before
        resolving; the deadline passing meanwhile must not override it."""
        registry: DecisionRegistry[Ask] = DecisionRegistry()
        ask = Ask("a")
        token = registry.register(ask)
        notice_sent = asyncio.Event()

        async def slow_reply() -> None:
            registry.try_claim(token)
            await notice_sent.wait()
            ask.future.set_result("accept")

        replier = asyncio.create_task(slow_reply())
        waiter = asyncio.create_task(registry.wait(token, ask.future, timeout_s=0.01))
        await asyncio.sleep(0.03)
        assert not waiter.done()

        notice_sent.set()
        assert await waiter == "accept"
        await replier

    async def test_evicting_a_waiter_resolves_it_with_the_forced_value(self) -> None:
        registry: DecisionRegistry[Ask] = DecisionRegistry(max_pending=1)
        first = Ask("first")
        first_token = registry.register(first)
        first_waiter = asyncio.create_task(
            registry.wait(first_token, first.future, timeout_s=1.0)
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
        token = registry.register(ask)
        waiter = asyncio.create_task(registry.wait(token, ask.future, timeout_s=1.0))
        await asyncio.sleep(0)

        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert token not in registry
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
            token = registry.register(ask, key=name)
            waiters[name] = asyncio.create_task(
                registry.wait(token, ask.future, timeout_s=0.05)
            )
            await asyncio.sleep(0)

        assert registry.claim_reply("d", None) is ClaimOutcome.CLAIMED
        asks["d"].future.set_result("accept")

        outcomes = {name: await waiter for name, waiter in waiters.items()}
        assert outcomes == {
            "a": "evicted",
            "b": "evicted",
            "c": Timeout.TIMED_OUT,
            "d": "accept",
        }
        assert not registry
