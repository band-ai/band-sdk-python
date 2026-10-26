"""Concurrent callback selections wait for the preceding transaction to settle."""

from __future__ import annotations

import asyncio

import pytest

from band.testing import FakeAgentTools
from tests.adapters.omp.mailboxpeer import MODEL_PROVIDER, ORIGINAL_MODEL
from tests.adapters.omp.selectionkit import (
    ACCEPTED_MODEL,
    SELECTED_EFFORT,
)
from tests.adapters.omp.turns import run_turn


class TestSelectionOverlap:
    @pytest.mark.asyncio
    async def test_accepted_selection_survives_an_overlapping_rollback(
        self, overlapping_selections
    ) -> None:
        adapter, runtime, resolver = overlapping_selections
        turn = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
        try:
            await resolver.waiter_ready.wait()
            assert resolver.waiter is not None
            assert not resolver.waiter.done()
            resolver.release.set()
            await turn
            assert isinstance(resolver.results[0], ValueError)
            assert resolver.results[1] is None

            tools = FakeAgentTools()
            await run_turn(adapter, tools)
            assert runtime.model == (MODEL_PROVIDER, ACCEPTED_MODEL)
            assert runtime.thinking == SELECTED_EFFORT
            assert tools.turn.replied
        finally:
            resolver.release.set()
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_cancelling_a_waiting_selection_keeps_the_restored_model(
        self, overlapping_selections
    ) -> None:
        adapter, runtime, resolver = overlapping_selections
        turn = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
        try:
            await resolver.waiter_ready.wait()
            assert resolver.waiter is not None
            resolver.waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await resolver.waiter
            resolver.release.set()
            await turn

            tools = FakeAgentTools()
            await run_turn(adapter, tools)
            assert runtime.model == (MODEL_PROVIDER, ORIGINAL_MODEL)
            assert runtime.thinking == SELECTED_EFFORT
            assert not runtime.closed
            assert tools.turn.replied
        finally:
            resolver.release.set()
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
