"""A real RPC client settles turns and restarts across generated host interleavings."""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given, settings

from tests.integrations.omprpc.lifecycle import (
    MODEL_GUARD_S,
    MODEL_PHASES,
    Step,
    operation_sequences,
    play,
)


class TestRpcLifecycleModel:
    @pytest.mark.timeout(MODEL_GUARD_S)
    @settings(
        max_examples=20,
        deadline=None,
        phases=MODEL_PHASES,
    )
    @given(steps=operation_sequences())
    def test_generated_operations_preserve_settlement_and_restart(
        self, steps: list[Step]
    ) -> None:
        asyncio.run(play(steps))
