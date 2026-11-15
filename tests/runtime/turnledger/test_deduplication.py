"""Deduplicated tools share the underlying turn ledger."""

from __future__ import annotations

from band.integrations.claude_sdk.dedup_tools import DedupingAgentTools
from band.testing.fake_tools import (
    FakeAgentTools,
)


class TestDeduplication:
    async def test_the_dedup_wrapper_shares_the_inner_ledger(self) -> None:
        inner = FakeAgentTools()
        wrapped = DedupingAgentTools(inner)

        await wrapped.send_message("hi", mentions=["@alice"])

        assert wrapped.turn is inner.turn
        assert inner.turn.replied
