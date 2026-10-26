"""Caller cancellation stays primary while surviving tools retain resources."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpProcessError
from tests.adapters.omp.completion import SurvivingToolContext
from tests.adapters.omp.turns import run_turn
from tests.baseline.tools import BaselineTools
from tests.integrations.omppeers.base import COMPLETE


class TestCompletionCancellation:
    async def test_cancelled_turn_retains_custody_until_callback_retirement(
        self, surviving_tool_turn: SurvivingToolContext
    ) -> None:
        async with surviving_tool_turn as turn:
            turn.running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await turn.running

            plugin = turn.clients.plugins[0]
            assert plugin.is_dir()
            with pytest.raises(OmpProcessError, match="retirement is unproven"):
                await run_turn(turn.adapter, BaselineTools(), bootstrap=False)
            assert turn.clients.clients == [turn.runtime]

            await turn.hold.release()
            await turn.hold.retired.wait()
            recovered = BaselineTools()
            await run_turn(turn.adapter, recovered, bootstrap=False)

            assert recovered.chat == [COMPLETE]
            assert not plugin.exists()
