"""DedupedSendBelongsToItsTurn behavior."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.claude_sdk.dedup_tools import (
    DedupingAgentTools,
)
from band.testing import FakeAgentTools
from tests.integrations.claude_sdk.dedup.kit import GatedSendTools


class TestDedupedSendBelongsToItsTurn:
    """A suppressed duplicate is the earlier turn's send re-issued: it posts
    nothing new, so it never counts as a later turn's reply."""

    @pytest.mark.asyncio
    async def test_a_cached_duplicate_does_not_answer_the_next_turn(self):
        first_turn = FakeAgentTools()
        wrapper = DedupingAgentTools(first_turn)
        await wrapper.send_message("hi", ["alice"])

        next_turn = FakeAgentTools()
        wrapper = wrapper.for_origin(next_turn)
        await wrapper.send_message("hi", ["alice"])

        assert first_turn.turn.replied
        assert not next_turn.turn.replied

    @pytest.mark.asyncio
    async def test_a_duplicate_joining_an_in_flight_send_does_not_answer_its_turn(
        self,
    ):
        first_turn = GatedSendTools()
        wrapper = DedupingAgentTools(first_turn)
        original = asyncio.create_task(wrapper.send_message("hi", ["alice"]))
        await first_turn.entered.wait()

        next_turn = FakeAgentTools()
        wrapper = wrapper.for_origin(next_turn)
        duplicate = asyncio.create_task(wrapper.send_message("hi", ["alice"]))
        first_turn.gate.set()
        await asyncio.gather(original, duplicate)

        assert first_turn.turn.replied
        assert not next_turn.turn.replied
