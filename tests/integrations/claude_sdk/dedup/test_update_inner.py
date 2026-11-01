"""UpdateInner behavior."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.integrations.claude_sdk.dedup_tools import (
    DedupingAgentTools,
)
from tests.integrations.claude_sdk.dedup.kit import _make_inner


class TestUpdateInner:
    """update_inner() swaps tools without discarding cache state.

    The adapter keeps one wrapper per room so lingering MCP retries can still
    resolve through ``_room_tools`` after the next inbound message has installed
    fresh per-message tools.
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
        wrapper = wrapper.for_origin(inner_b)

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
        wrapper = wrapper.for_origin(inner_b)

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
        # Yield so send_task registers the in-flight task and awaits the gate.
        await entered.wait()

        inner_b = _make_inner()
        next_view = wrapper.for_origin(inner_b)
        await next_view.send_message("new origin", ["alice"])

        gate.set()
        await send_task
        assert observed_inner == ["a"]
        assert inner_b.send_message.await_count == 1
