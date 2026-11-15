"""Generated shutdown, retry, concurrent caller and settlement sequences."""

from __future__ import annotations

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from tests.runtime.executioninterrupt.shutdown import ShutdownStep, shutdown_scenario
from tests.toolkit.clock import binary_clock_resolution


class TestShutdownModel:
    @pytest.mark.looptime(resolution=binary_clock_resolution())
    @settings(max_examples=20, deadline=None)
    @given(steps=st.lists(st.sampled_from(ShutdownStep), min_size=1, max_size=6))
    @example(steps=[ShutdownStep.STOP, ShutdownStep.STOP, ShutdownStep.SETTLE])
    @example(steps=[ShutdownStep.JOIN_STOP, ShutdownStep.SETTLE, ShutdownStep.STOP])
    async def test_generated_steps_preserve_cancellation_ownership(
        self, steps: list[ShutdownStep]
    ) -> None:
        async with shutdown_scenario() as scenario:
            for step in steps:
                await scenario.take(step)
