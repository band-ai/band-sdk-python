"""Tests for DedupingAgentTools (send_message dedup shim)."""

from __future__ import annotations

import asyncio
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
        # the wrapper and contend on the lock.
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
        await wrapper.add_participant(handle="@svc/bot")

        inner.send_event.assert_awaited_once_with(
            content="ping", message_type="thought"
        )
        inner.add_participant.assert_awaited_once_with(handle="@svc/bot")

    def test_attributes_forward_unchanged(self):
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)
        assert wrapper.participants == ["p1", "p2"]
