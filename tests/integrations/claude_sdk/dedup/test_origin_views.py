"""Immutable origin views share deduplication without redirecting delivery."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.integrations.claude_sdk.dedup_tools import (
    DedupingAgentTools,
)
from tests.integrations.claude_sdk.dedup.kit import _make_inner


class TestOriginViews:
    @pytest.mark.asyncio
    async def test_duplicate_is_suppressed_across_origins(self):
        inner_a = _make_inner()
        wrapper = DedupingAgentTools(inner_a)

        await wrapper.send_message("hi", ["alice"])
        assert inner_a.send_message.await_count == 1

        inner_b = _make_inner()
        wrapper = wrapper.for_origin(inner_b)

        await wrapper.send_message("hi", ["alice"])
        assert inner_b.send_message.await_count == 0

    @pytest.mark.asyncio
    async def test_new_sends_use_their_captured_origin(self):
        """Route each novel send through its immutable origin view."""
        inner_a = _make_inner()
        wrapper = DedupingAgentTools(inner_a)
        await wrapper.send_message("first", ["alice"])

        inner_b = _make_inner()
        wrapper = wrapper.for_origin(inner_b)

        await wrapper.send_message("second", ["alice"])
        assert inner_b.send_message.await_count == 1
        assert inner_a.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_new_origin_does_not_redirect_in_flight_send(self):
        """Allow new work while the original delivery remains blocked."""
        inner_a = _make_inner()
        gate = asyncio.Event()
        entered = asyncio.Event()
        observed_inner: list[Any] = []

        async def slow_send(content, mentions=None):
            observed_inner.append("a")
            entered.set()
            await gate.wait()
            return {"id": "msg-a"}

        inner_a.send_message.side_effect = slow_send
        wrapper = DedupingAgentTools(inner_a)

        send_task = asyncio.create_task(wrapper.send_message("hi", ["alice"]))
        await entered.wait()

        inner_b = _make_inner()
        next_view = wrapper.for_origin(inner_b)
        await next_view.send_message("new origin", ["alice"])

        gate.set()
        await send_task
        assert observed_inner == ["a"]
        assert inner_b.send_message.await_count == 1
