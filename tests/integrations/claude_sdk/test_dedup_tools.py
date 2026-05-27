"""Tests for DedupingAgentTools (send_message dedup shim)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.integrations.claude_sdk.dedup_tools import (
    DEFAULT_DEDUP_MAX_ENTRIES,
    DEFAULT_DEDUP_TTL_SECONDS,
    DedupingAgentTools,
)


def _make_inner() -> MagicMock:
    """Inner AgentToolsProtocol stub. send_message returns a unique result."""
    inner = MagicMock()
    inner.send_message = AsyncMock(return_value={"id": "msg-1"})
    inner.send_event = AsyncMock(return_value={"id": "evt-1"})
    inner.add_participant = AsyncMock(return_value={"id": "u"})
    inner.participants = ["p1", "p2"]
    return inner


class TestDedupingAgentToolsConstruction:
    def test_invalid_ttl_rejected(self):
        with pytest.raises(ValueError):
            DedupingAgentTools(_make_inner(), ttl_seconds=0)
        with pytest.raises(ValueError):
            DedupingAgentTools(_make_inner(), ttl_seconds=-1)

    def test_invalid_max_entries_rejected(self):
        with pytest.raises(ValueError):
            DedupingAgentTools(_make_inner(), max_entries=0)
        with pytest.raises(ValueError):
            DedupingAgentTools(_make_inner(), max_entries=-5)

    def test_defaults_are_set(self):
        wrapper = DedupingAgentTools(_make_inner())
        assert wrapper._ttl_seconds == DEFAULT_DEDUP_TTL_SECONDS
        assert wrapper._max_entries == DEFAULT_DEDUP_MAX_ENTRIES


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
    async def test_distinct_dedup_scope_does_not_dedup(self):
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)

        await wrapper.update_inner(inner, dedup_scope="platform-msg-1")
        await wrapper.send_message("Done.", ["alice"])
        await wrapper.update_inner(inner, dedup_scope="platform-msg-2")
        await wrapper.send_message("Done.", ["alice"])

        assert inner.send_message.await_count == 2

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
    async def test_expiry_allows_resend(self, monkeypatch):
        """After the TTL window, an identical send must go through again."""
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner, ttl_seconds=1.0)

        clock = {"t": 1000.0}
        monkeypatch.setattr(
            "band.integrations.claude_sdk.dedup_tools.time.monotonic",
            lambda: clock["t"],
        )

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

        async def slow_send(content, mentions=None):
            await gate.wait()
            return {"id": "msg-1"}

        inner.send_message.side_effect = slow_send
        wrapper = DedupingAgentTools(inner)

        t1 = asyncio.create_task(wrapper.send_message("hi", ["alice"]))
        t2 = asyncio.create_task(wrapper.send_message("hi", ["alice"]))
        # Let both tasks run up to their first await point.
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(t1, t2)

        assert inner.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_distinct_concurrent_sends_do_not_block_each_other(self):
        inner = _make_inner()
        slow_gate = asyncio.Event()
        completed: list[str] = []

        async def controlled_send(content, mentions=None):
            if content == "slow":
                await slow_gate.wait()
            completed.append(content)
            return {"id": content}

        inner.send_message.side_effect = controlled_send
        wrapper = DedupingAgentTools(inner)

        slow_task = asyncio.create_task(wrapper.send_message("slow", ["alice"]))
        await asyncio.sleep(0)
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


class TestTransparentPassthrough:
    @pytest.mark.asyncio
    async def test_other_methods_forward_unchanged(self):
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)

        await wrapper.send_event(content="ping", message_type="thought")
        await wrapper.add_participant("@svc/bot")

        inner.send_event.assert_awaited_once_with(
            content="ping", message_type="thought"
        )
        inner.add_participant.assert_awaited_once_with("@svc/bot")

    def test_attributes_forward_unchanged(self):
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)
        assert wrapper.participants == ["p1", "p2"]


class TestUpdateInner:
    """update_inner() swaps tools without discarding cache state.

    The adapter keeps one wrapper per room so lingering MCP retries can still
    resolve through ``_room_tools``. The adapter also supplies a per-message
    dedup scope; tests that omit it exercise the backwards-compatible default
    scope.
    """

    @pytest.mark.asyncio
    async def test_dedup_survives_inner_swap(self):
        inner_a = _make_inner()
        wrapper = DedupingAgentTools(inner_a)

        # Turn 1: original send populates the cache via inner_a.
        await wrapper.send_message("hi", ["alice"])
        assert inner_a.send_message.await_count == 1

        # Turn 2: SimpleAdapter would normally build a fresh AgentTools
        # and the adapter rebuilds wrappers per call. With update_inner,
        # the wrapper persists and the cache is intact.
        inner_b = _make_inner()
        await wrapper.update_inner(inner_b)

        # The duplicate tool call from the previous turn fires now.
        # It must hit the cache and NOT POST through inner_b.
        await wrapper.send_message("hi", ["alice"])
        assert inner_b.send_message.await_count == 0

    @pytest.mark.asyncio
    async def test_new_sends_use_new_inner(self):
        """After swap, novel sends route to the new inner tools."""
        inner_a = _make_inner()
        wrapper = DedupingAgentTools(inner_a)
        await wrapper.send_message("first", ["alice"])

        inner_b = _make_inner()
        await wrapper.update_inner(inner_b)

        await wrapper.send_message("second", ["alice"])
        assert inner_b.send_message.await_count == 1
        # The first inner is no longer used after the swap.
        assert inner_a.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_swap_does_not_wait_for_in_flight_send(self):
        """update_inner is not serialized behind platform I/O.

        An in-flight send captures the inner tools before awaiting the POST, so
        update_inner can install the next turn's tools immediately without
        changing where that in-flight send lands.
        """
        inner_a = _make_inner()
        gate = asyncio.Event()
        observed_inner: list[Any] = []

        async def slow_send(content, mentions=None):
            observed_inner.append("a")
            await gate.wait()
            return {"id": "msg-a"}

        inner_a.send_message.side_effect = slow_send
        wrapper = DedupingAgentTools(inner_a)

        send_task = asyncio.create_task(wrapper.send_message("hi", ["alice"]))
        # Yield so send_task registers the in-flight task and awaits the gate.
        await asyncio.sleep(0)

        inner_b = _make_inner()
        swap_task = asyncio.create_task(wrapper.update_inner(inner_b))

        await asyncio.sleep(0)
        assert swap_task.done()

        gate.set()
        await asyncio.gather(send_task, swap_task)
        assert observed_inner == ["a"]
        assert inner_b.send_message.await_count == 0


class TestDedupHitLogging:
    @pytest.mark.asyncio
    async def test_duplicate_emits_warning(self, caplog):
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)

        await wrapper.send_message("hello", ["alice"])
        with caplog.at_level(
            "WARNING", logger="thenvoi.integrations.claude_sdk.dedup_tools"
        ):
            await wrapper.send_message("hello", ["alice"])

        assert any(
            "dedup" in rec.message.lower() and rec.levelname == "WARNING"
            for rec in caplog.records
        ), (
            f"expected WARNING log on dedup hit, got: {[r.message for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_warning_includes_label_when_provided(self, caplog):
        """The adapter passes ``label=room_id`` so an operator triaging a
        dedup storm in production can map a warning back to one room.
        Without it the log is ``suppressing duplicate send`` with no
        identifier and is useless across a multi-room tenant.
        """
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner, label="room-abc")

        await wrapper.send_message("hello", ["alice"])
        with caplog.at_level(
            "WARNING", logger="thenvoi.integrations.claude_sdk.dedup_tools"
        ):
            await wrapper.send_message("hello", ["alice"])

        assert any("room-abc" in rec.getMessage() for rec in caplog.records), (
            f"expected room-abc label in warning, got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_warning_omits_label_brackets_when_unset(self, caplog):
        """No label → no empty ``[]`` in the log line."""
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)

        await wrapper.send_message("hello", ["alice"])
        with caplog.at_level(
            "WARNING", logger="thenvoi.integrations.claude_sdk.dedup_tools"
        ):
            await wrapper.send_message("hello", ["alice"])

        for rec in caplog.records:
            if "dedup" in rec.getMessage().lower():
                assert "[]" not in rec.getMessage()


class TestInnerSendFailure:
    """Behavior when the wrapped ``send_message`` raises.

    A failed POST should not poison the cache: the next attempt at the
    same ``(content, mentions)`` must be allowed to reach the platform,
    otherwise a transient platform error would silently swallow every
    subsequent send for the next ``ttl_seconds``.  These tests pin that
    contract so a future cache refactor cannot regress it without making
    the change visible.
    """

    @pytest.mark.asyncio
    async def test_inner_exception_propagates(self):
        inner = _make_inner()
        inner.send_message = AsyncMock(side_effect=RuntimeError("boom"))
        wrapper = DedupingAgentTools(inner)

        with pytest.raises(RuntimeError, match="boom"):
            await wrapper.send_message("hi", ["alice"])

    @pytest.mark.asyncio
    async def test_inner_failure_does_not_poison_cache(self):
        """A retry after a transient failure must reach the inner tools.

        If the wrapper kept a cache entry for the failed call, a follow-up
        retry would dedup against nothing useful and return a stale or
        bogus value.  The current implementation only writes the cache
        after a successful return, so the retry takes the cache-miss
        branch and POSTs again — which is correct.
        """
        inner = _make_inner()
        calls: list[int] = [0]

        async def flaky(content, mentions=None):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("transient")
            return {"id": "msg-recovered"}

        inner.send_message = AsyncMock(side_effect=flaky)
        wrapper = DedupingAgentTools(inner)

        with pytest.raises(RuntimeError):
            await wrapper.send_message("hi", ["alice"])

        result = await wrapper.send_message("hi", ["alice"])
        assert result == {"id": "msg-recovered"}
        assert calls[0] == 2

    @pytest.mark.asyncio
    async def test_recovered_call_then_dedupes_subsequent_duplicate(self):
        """After the recovery POST succeeds, the wrapper resumes deduping
        subsequent duplicates against the recovered result."""
        inner = _make_inner()
        calls: list[int] = [0]

        async def flaky(content, mentions=None):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("transient")
            return {"id": "msg-recovered"}

        inner.send_message = AsyncMock(side_effect=flaky)
        wrapper = DedupingAgentTools(inner)

        with pytest.raises(RuntimeError):
            await wrapper.send_message("hi", ["alice"])
        await wrapper.send_message("hi", ["alice"])  # recovery POST
        await wrapper.send_message("hi", ["alice"])  # dedup hit

        assert calls[0] == 2
