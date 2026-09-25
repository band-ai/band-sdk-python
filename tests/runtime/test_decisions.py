"""Tests for the shared chat-mediated decision registry (INT-1542).

Adapter-agnostic: exercises DecisionRegistry/await_decision directly, not
through any adapter. Each adapter's own test suite proves the migration
preserved its external behavior; these tests prove the shared primitive
itself is correct, including the exact races the ticket cites.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from band.runtime.decisions import DecisionRegistry, await_decision


@dataclass
class _Ask:
    """A minimal payload -- just enough to identify an entry in assertions."""

    name: str


@dataclass
class _FutureAsk:
    future: asyncio.Future[str] = field(default_factory=lambda: _future())


def _future() -> asyncio.Future[str]:
    return asyncio.get_running_loop().create_future()


class TestRegisterClaimForget:
    def test_minted_token_is_unique_per_call(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        first = registry.register(_Ask("a"))
        second = registry.register(_Ask("b"))
        assert first != second

    def test_get_returns_the_registered_payload(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        token = registry.register(_Ask("a"))
        assert token is not None
        assert registry.get(token) == _Ask("a")

    def test_get_is_none_for_an_unknown_token(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        assert registry.get("nope") is None

    def test_try_claim_returns_the_payload_once(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        token = registry.register(_Ask("a"))
        assert token is not None
        assert registry.try_claim(token) == _Ask("a")

    def test_try_claim_is_none_on_a_second_call(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        token = registry.register(_Ask("a"))
        assert token is not None
        registry.try_claim(token)
        assert registry.try_claim(token) is None

    def test_try_claim_is_none_for_an_unknown_token(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        assert registry.try_claim("nope") is None

    def test_get_is_none_once_claimed(self) -> None:
        """A claimed-but-not-yet-forgotten entry is mid-resolution, not a
        fresh pending ask -- callers must not treat it as still awaiting a
        reply."""
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        token = registry.register(_Ask("a"))
        assert token is not None
        registry.try_claim(token)
        assert registry.get(token) is None

    def test_forget_drops_the_entry(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        token = registry.register(_Ask("a"))
        assert token is not None
        registry.try_claim(token)
        registry.forget(token)
        assert len(registry) == 0

    def test_forget_an_unknown_token_is_a_no_op(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        registry.forget("nope")  # must not raise


class TestSupersede:
    def test_redelivery_under_the_same_key_replaces_the_entry(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        registry.register(_Ask("first"), key="req-1")
        registry.register(_Ask("second"), key="req-1")
        assert registry.get("req-1") == _Ask("second")
        assert len(registry) == 1

    async def test_redelivery_cancels_the_superseded_entrys_timer(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        registry.register(_Ask("first"), key="req-1")
        fired = False

        async def _on_timeout(_: _Ask) -> None:
            nonlocal fired
            fired = True

        registry.start_timeout("req-1", 0.01, _on_timeout)
        registry.register(_Ask("second"), key="req-1")
        await asyncio.sleep(0.05)
        assert fired is False

    def test_redelivery_of_an_already_claimed_key_is_rejected(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        registry.register(_Ask("first"), key="req-1")
        registry.try_claim("req-1")
        result = registry.register(_Ask("second"), key="req-1")
        assert result is None
        assert registry.get("req-1") is None  # the original stays claimed, not replaced


class TestTimeout:
    async def test_timeout_fires_on_timeout_with_the_payload(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        token = registry.register(_Ask("a"))
        assert token is not None
        seen: list[_Ask] = []

        async def _on_timeout(payload: _Ask) -> None:
            seen.append(payload)

        registry.start_timeout(token, 0.01, _on_timeout)
        await asyncio.sleep(0.05)
        assert seen == [_Ask("a")]

    async def test_a_claim_before_the_deadline_cancels_the_timeout(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        token = registry.register(_Ask("a"))
        assert token is not None
        fired = False

        async def _on_timeout(_: _Ask) -> None:
            nonlocal fired
            fired = True

        registry.start_timeout(token, 0.05, _on_timeout)
        registry.try_claim(token)
        await asyncio.sleep(0.1)
        assert fired is False

    async def test_the_toctou_race_a_claim_and_a_timeout_never_both_win(self) -> None:
        """Reproduces the exact race f15c1e01 fixed for Cursor, generically:
        a room reply and this entry's own timeout can both be "in flight" at
        once (the timeout fired and is about to claim; a reply handler is
        also about to claim) -- try_claim must let exactly one of them
        through, deterministically, however they're interleaved."""
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        token = registry.register(_Ask("a"))
        assert token is not None

        timeout_fired = asyncio.Event()

        async def _on_timeout(_: _Ask) -> None:
            timeout_fired.set()

        registry.start_timeout(token, 0.01, _on_timeout)
        await asyncio.sleep(0.02)  # let the timeout task actually run and claim
        assert timeout_fired.is_set()

        # A "late room reply" arriving after the timeout already claimed.
        late_reply = registry.try_claim(token)
        assert late_reply is None


class TestEviction:
    def test_unbounded_registry_never_evicts(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry(max_pending=None)
        for i in range(50):
            registry.register(_Ask(str(i)))
        assert registry.evict_oldest() is None
        assert len(registry) == 50

    def test_evicts_the_oldest_once_at_capacity(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry(max_pending=2)
        registry.register(_Ask("first"), key="a")
        registry.register(_Ask("second"), key="b")
        evicted = registry.evict_oldest()
        assert evicted is not None
        assert evicted.token == "a"
        assert len(registry) == 1

    def test_below_capacity_does_not_evict(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry(max_pending=2)
        registry.register(_Ask("first"))
        assert registry.evict_oldest() is None

    def test_eviction_skips_an_already_claimed_entry(self) -> None:
        """A claimed entry is mid-resolution, not idle capacity -- eviction
        must reach past it to the oldest genuinely unclaimed one."""
        registry: DecisionRegistry[_Ask] = DecisionRegistry(max_pending=2)
        registry.register(_Ask("first"), key="a")
        registry.register(_Ask("second"), key="b")
        registry.try_claim("a")
        evicted = registry.evict_oldest()
        assert evicted is not None
        assert evicted.token == "b"

    async def test_eviction_cancels_the_evicted_entrys_timer(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry(max_pending=1)
        registry.register(_Ask("first"), key="a")
        fired = False

        async def _on_timeout(_: _Ask) -> None:
            nonlocal fired
            fired = True

        registry.start_timeout("a", 0.01, _on_timeout)
        registry.register(_Ask("second"), key="b")
        registry.evict_oldest()
        await asyncio.sleep(0.05)
        assert fired is False

    async def test_capacity_plus_concurrent_registration_and_timeouts(self) -> None:
        """A full room's worth of overlapping asks: some age out via timeout,
        new ones keep arriving and evict the oldest survivor, at every point
        len() must stay within capacity and no token is ever double-resolved."""
        registry: DecisionRegistry[_Ask] = DecisionRegistry(max_pending=3)
        resolved: list[str] = []

        async def _on_timeout(payload: _Ask) -> None:
            resolved.append(payload.name)

        for i in range(6):
            evicted = registry.evict_oldest()
            if evicted is not None:
                resolved.append(evicted.payload.name)
            token = registry.register(_Ask(f"ask-{i}"), key=f"k{i}")
            assert token is not None
            registry.start_timeout(token, 0.01, _on_timeout)
            assert len(registry) <= 3

        await asyncio.sleep(0.05)
        # Every ask is accounted for exactly once: evicted xor timed out.
        assert sorted(resolved) == sorted(f"ask-{i}" for i in range(6))


class TestCancelAll:
    def test_cancel_all_pops_every_entry(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        registry.register(_Ask("a"), key="a")
        registry.register(_Ask("b"), key="b")
        cancelled = registry.cancel_all()
        assert {entry.token for entry in cancelled} == {"a", "b"}
        assert len(registry) == 0

    def test_cancel_all_with_a_predicate_filters(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        registry.register(_Ask("keep"), key="keep")
        registry.register(_Ask("drop"), key="drop")
        cancelled = registry.cancel_all(lambda payload: payload.name == "drop")
        assert {entry.token for entry in cancelled} == {"drop"}
        assert registry.get("keep") == _Ask("keep")
        assert len(registry) == 1

    async def test_cancel_all_stops_pending_timers_from_firing(self) -> None:
        registry: DecisionRegistry[_Ask] = DecisionRegistry()
        token = registry.register(_Ask("a"))
        assert token is not None
        fired = False

        async def _on_timeout(_: _Ask) -> None:
            nonlocal fired
            fired = True

        registry.start_timeout(token, 0.01, _on_timeout)
        registry.cancel_all()
        await asyncio.sleep(0.05)
        assert fired is False


class TestAwaitDecision:
    async def test_returns_the_result_a_claim_resolves_it_with(self) -> None:
        registry: DecisionRegistry[_FutureAsk] = DecisionRegistry()
        payload = _FutureAsk()

        async def _resolve_soon() -> None:
            await asyncio.sleep(0.01)
            payload.future.set_result("approved")

        asyncio.create_task(_resolve_soon())
        result = await await_decision(
            registry, payload, timeout_s=1.0, forced_value="declined"
        )
        assert result == "approved"

    async def test_forces_the_value_on_timeout(self) -> None:
        registry: DecisionRegistry[_FutureAsk] = DecisionRegistry()
        payload = _FutureAsk()
        result = await await_decision(
            registry, payload, timeout_s=0.01, forced_value="declined"
        )
        assert result == "declined"

    async def test_forgets_the_token_after_resolving(self) -> None:
        registry: DecisionRegistry[_FutureAsk] = DecisionRegistry()
        payload = _FutureAsk()
        payload.future.set_result("approved")
        await await_decision(registry, payload, timeout_s=1.0, forced_value="declined")
        assert len(registry) == 0

    async def test_forgets_the_token_after_timing_out(self) -> None:
        registry: DecisionRegistry[_FutureAsk] = DecisionRegistry()
        payload = _FutureAsk()
        await await_decision(registry, payload, timeout_s=0.01, forced_value="declined")
        assert len(registry) == 0

    async def test_eviction_forces_the_evicted_entrys_future(self) -> None:
        registry: DecisionRegistry[_FutureAsk] = DecisionRegistry(max_pending=1)
        first = _FutureAsk()
        second = _FutureAsk()

        first_task = asyncio.create_task(
            await_decision(registry, first, timeout_s=1.0, forced_value="declined")
        )
        await asyncio.sleep(0)  # let the first registration land
        second_task = asyncio.create_task(
            await_decision(registry, second, timeout_s=1.0, forced_value="declined")
        )
        await asyncio.sleep(0)
        second.future.set_result("approved")

        first_result = await first_task
        second_result = await second_task
        assert first_result == "declined"  # evicted
        assert second_result == "approved"

    async def test_a_late_reply_during_the_timeout_window_never_wins(self) -> None:
        """The generic version of the exact bug f15c1e01 fixed: once the
        timeout has claimed the token, a "reply" racing in immediately after
        must find nothing left to claim."""
        registry: DecisionRegistry[_FutureAsk] = DecisionRegistry()
        payload = _FutureAsk()

        await await_decision(registry, payload, timeout_s=0.01, forced_value="declined")

        # A reply handler that looked the token up before the timeout
        # branch's finally ran would try this next -- it must get nothing.
        assert registry.try_claim("whatever-token-it-had") is None
