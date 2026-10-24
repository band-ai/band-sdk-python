"""Exercise generated operation combinations against real descendant trees."""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.integrations.omprpc.lifecycle import (
    MODEL_GUARD_S,
    MODEL_PHASES,
    Step,
    operation_sequences,
    play_tree,
)


class TestRpcTreeLifecycleModel:
    @pytest.mark.timeout(MODEL_GUARD_S)
    @settings(max_examples=6, deadline=None, phases=MODEL_PHASES)
    @given(steps=operation_sequences(min_steps=3), detached=st.booleans())
    def test_generated_operations_reap_owned_trees_and_recover(
        self, steps: list[Step], detached: bool
    ) -> None:
        asyncio.run(play_tree(steps, detached=detached))
