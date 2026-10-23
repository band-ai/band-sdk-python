"""A real RPC client settles turns and restarts across generated host interleavings."""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import Phase, given, settings

from tests.integrations.omprpc.lifecycle import (
    Step,
    play,
    sequences,
)


class TestRpcLifecycleModel:
    # Descendant containment belongs to the real-tree regression scenarios.
    @pytest.mark.timeout(600)
    @settings(
        max_examples=20,
        deadline=None,
        phases=(Phase.reuse, Phase.generate),
    )
    @given(steps=sequences)
    def test_generated_operations_preserve_settlement_and_restart(
        self, steps: list[Step]
    ) -> None:
        asyncio.run(play(steps))
