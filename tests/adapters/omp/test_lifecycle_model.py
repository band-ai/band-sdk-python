from __future__ import annotations

import asyncio

from hypothesis import Phase, example, given, settings
from hypothesis import strategies as st

from tests.adapters.omp.lifecycle import Step, play


class TestRoomLifecycleModel:
    @settings(
        max_examples=20,
        deadline=None,
        phases=(Phase.explicit, Phase.reuse, Phase.generate),
    )
    @given(steps=st.lists(st.sampled_from(Step), min_size=1, max_size=10))
    @example(steps=[Step.TURN, Step.FAILED_CLOSE, Step.TURN, Step.CLEANUP])
    @example(
        steps=[Step.RETIRE_QUEUED, Step.TURN, Step.CANCEL_CLEANUP_WAITER, Step.TURN]
    )
    def test_sequences_preserve_runtime_and_workspace_custody(
        self, steps: list[Step]
    ) -> None:
        asyncio.run(play(steps))
