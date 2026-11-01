"""SendMessageDedup behavior."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.claude_sdk.dedup_tools import (
    DedupingAgentTools,
)
from tests.integrations.claude_sdk.dedup.kit import _make_inner


class TestSendMessageDedup:
    @pytest.mark.asyncio
    async def test_identical_calls_collapse_to_one_inner_post(self):
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)

        r1 = await wrapper.send_message("hello", ["alice"])
        r2 = await wrapper.send_message("hello", ["alice"])

        assert inner.send_message.await_count == 1
        # Cached result is returned verbatim.
        assert r1 == r2 == {"id": "msg-1"}

    @pytest.mark.asyncio
    async def test_distinct_content_does_not_dedup(self):
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)

        await wrapper.send_message("hello", ["alice"])
        await wrapper.send_message("hello world", ["alice"])

        assert inner.send_message.await_count == 2

    @pytest.mark.asyncio
    async def test_distinct_mentions_does_not_dedup(self):
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)

        await wrapper.send_message("hello", ["alice"])
        await wrapper.send_message("hello", ["bob"])

        assert inner.send_message.await_count == 2

    @pytest.mark.asyncio
    async def test_inner_swap_does_not_bypass_late_retry_dedup(self):
        inner_a = _make_inner()
        wrapper = DedupingAgentTools(inner_a)

        await wrapper.send_message("Done.", ["alice"])
        inner_b = _make_inner()
        wrapper = wrapper.for_origin(inner_b)
        await wrapper.send_message("Done.", ["alice"])

        assert inner_a.send_message.await_count == 1
        assert inner_b.send_message.await_count == 0

    @pytest.mark.asyncio
    async def test_mention_order_does_not_matter(self):
        """A retry that re-orders the mentions list is the same logical send."""
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)

        await wrapper.send_message("hi", ["alice", "bob"])
        await wrapper.send_message("hi", ["bob", "alice"])

        assert inner.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_dict_mentions_normalize_to_same_key(self):
        """Both list[str] and list[dict] mention shapes share one cache key."""
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)

        await wrapper.send_message("hi", ["alice"])
        await wrapper.send_message("hi", [{"handle": "alice", "id": "u-1"}])

        assert inner.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_empty_and_none_mentions_share_key(self):
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)

        await wrapper.send_message("hi", None)
        await wrapper.send_message("hi", [])

        assert inner.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_expiry_allows_resend(self):
        """After the TTL window, an identical send must go through again."""
        inner = _make_inner()
        clock = {"t": 1000.0}
        wrapper = DedupingAgentTools(inner, ttl_seconds=1.0, clock=lambda: clock["t"])

        await wrapper.send_message("hi", ["alice"])
        clock["t"] += 0.5
        await wrapper.send_message("hi", ["alice"])  # within TTL → cached
        clock["t"] += 1.0  # cross the TTL boundary
        await wrapper.send_message("hi", ["alice"])

        assert inner.send_message.await_count == 2

    @pytest.mark.asyncio
    async def test_serializes_concurrent_duplicates(self):
        """Two coroutines racing on the same key must POST exactly once."""
        inner = _make_inner()
        # Block the first inner call long enough for the second to enter
        # the wrapper and observe the in-flight task.
        gate = asyncio.Event()
        entered = asyncio.Event()

        async def slow_send(content, mentions=None):
            entered.set()
            await gate.wait()
            return {"id": "msg-1"}

        inner.send_message.side_effect = slow_send
        wrapper = DedupingAgentTools(inner)

        t1 = asyncio.create_task(wrapper.send_message("hi", ["alice"]))
        t2 = asyncio.create_task(wrapper.send_message("hi", ["alice"]))
        # Let both tasks run up to their first await point.
        gate.set()
        await asyncio.gather(t1, t2)

        assert inner.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_cancelled_waiter_does_not_poison_in_flight_cache(self):
        """Caller cancellation must not leave a stale in-flight task.

        Claude SDK transports can cancel a request while the underlying POST is
        still running. The in-flight dedup entry should keep tracking that POST,
        let the next identical caller await it, then move the successful result
        into the completed-send cache.
        """
        inner = _make_inner()
        gate = asyncio.Event()
        entered = asyncio.Event()

        async def slow_send(content, mentions=None):
            entered.set()
            await gate.wait()
            return {"id": "msg-after-cancel"}

        inner.send_message.side_effect = slow_send
        wrapper = DedupingAgentTools(inner)

        first_waiter = asyncio.create_task(wrapper.send_message("hi", ["alice"]))
        await entered.wait()
        first_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_waiter

        second_waiter = asyncio.create_task(wrapper.send_message("hi", ["alice"]))
        gate.set()
        result = await second_waiter

        assert result == {"id": "msg-after-cancel"}
        assert inner.send_message.await_count == 1
        await wrapper.send_message("hi", ["alice"])
        assert inner.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_in_flight_send_expires_at_ttl(self):
        """A stalled POST must not extend the dedup window forever."""
        inner = _make_inner()
        gate = asyncio.Event()
        entered = asyncio.Event()
        calls: list[int] = []

        async def slow_first_send(content, mentions=None):
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                entered.set()
                await gate.wait()
                return {"id": "first"}
            return {"id": "second"}

        inner.send_message.side_effect = slow_first_send
        clock = {"t": 1000.0}
        wrapper = DedupingAgentTools(inner, ttl_seconds=1.0, clock=lambda: clock["t"])

        first_waiter = asyncio.create_task(wrapper.send_message("hi", ["alice"]))
        await entered.wait()
        clock["t"] += 1.1

        second_result = await wrapper.send_message("hi", ["alice"])
        assert second_result == {"id": "second"}
        assert inner.send_message.await_count == 2

        gate.set()
        assert await first_waiter == {"id": "first"}

    @pytest.mark.asyncio
    async def test_distinct_concurrent_sends_do_not_block_each_other(self):
        inner = _make_inner()
        slow_gate = asyncio.Event()
        entered = asyncio.Event()
        completed: list[str] = []

        async def controlled_send(content, mentions=None):
            if content == "slow":
                entered.set()
                await slow_gate.wait()
            completed.append(content)
            return {"id": content}

        inner.send_message.side_effect = controlled_send
        wrapper = DedupingAgentTools(inner)

        slow_task = asyncio.create_task(wrapper.send_message("slow", ["alice"]))
        await entered.wait()
        fast_result = await wrapper.send_message("fast", ["alice"])

        assert fast_result == {"id": "fast"}
        assert completed == ["fast"]

        slow_gate.set()
        await slow_task
        assert completed == ["fast", "slow"]

    @pytest.mark.asyncio
    async def test_cache_bounded_by_max_entries(self):
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner, max_entries=3)

        for i in range(5):
            await wrapper.send_message(f"msg-{i}", ["alice"])

        # Only the 3 most recent entries should remain. The first two
        # should be re-sent (cache miss) on replay.
        before = inner.send_message.await_count
        await wrapper.send_message("msg-0", ["alice"])
        await wrapper.send_message("msg-1", ["alice"])
        assert inner.send_message.await_count == before + 2
