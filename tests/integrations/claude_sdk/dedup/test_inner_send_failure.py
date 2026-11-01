"""InnerSendFailure behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.integrations.claude_sdk.dedup_tools import (
    DedupingAgentTools,
)
from tests.integrations.claude_sdk.dedup.kit import _make_inner


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
