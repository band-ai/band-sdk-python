from __future__ import annotations

import asyncio

import pytest
from hypothesis import Phase, example, given, settings
from hypothesis import strategies as st

from tests.adapters.omp.lifecycle import NativeStep, Step, play, play_native


class TestRoomLifecycleModel:
    @pytest.mark.timeout(90)
    @settings(
        max_examples=8,
        deadline=None,
        phases=(Phase.explicit, Phase.reuse, Phase.generate),
    )
    @given(steps=st.lists(st.sampled_from(NativeStep), min_size=1, max_size=6))
    @example(steps=[NativeStep.TURN, NativeStep.SELECT, NativeStep.TURN])
    @example(
        steps=[NativeStep.TURN, NativeStep.CLEANUP, NativeStep.SELECT, NativeStep.TURN]
    )
    @example(steps=[NativeStep.SELECT, NativeStep.CLEANUP, NativeStep.TURN])
    def test_real_callbacks_preserve_selection_and_release_native_resources(
        self, steps: list[NativeStep]
    ) -> None:
        asyncio.run(play_native(steps))

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
