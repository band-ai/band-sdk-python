"""Owned process trees and admitted turns survive generated host interleavings."""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import Phase, example, given, settings

from tests.integrations.omprpc.lifecycle import Step, play, sequences


class TestRpcLifecycleModel:
    @pytest.mark.parametrize("detached", [False, True], ids=["session", "detached"])
    # The generated workload launches hundreds of real peers; each step stays guarded.
    @pytest.mark.timeout(120)
    @settings(
        max_examples=20,
        deadline=None,
        phases=(Phase.explicit, Phase.reuse, Phase.generate),
    )
    @given(steps=sequences)
    @example(steps=[Step.PROMPT, Step.CANCEL_QUEUED, Step.SETTLE])
    @example(steps=[Step.PROMPT, Step.CRASH, Step.PROMPT, Step.CANCEL_OWNER])
    @example(steps=[Step.CANCEL_START_RESTART, Step.PROMPT, Step.CLOSE])
    @example(steps=[Step.CANCEL_CLOSE_RESTART, Step.PROMPT, Step.SETTLE])
    @example(steps=[Step.PROMPT, Step.PROMPT, Step.CANCEL_OWNER, Step.CANCEL_OWNER])
    @example(
        steps=[
            Step.CANCEL_CLOSE_RESTART,
            Step.CANCEL_OWNER,
            Step.CANCEL_CLOSE_RESTART,
            Step.CANCEL_CLOSE_RESTART,
            Step.CANCEL_OWNER,
            Step.CANCEL_OWNER,
        ]
    )
    def test_generated_operations_preserve_custody_and_settlement(
        self, steps: list[Step], detached: bool
    ) -> None:
        asyncio.run(play(steps, detached=detached))
