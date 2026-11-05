"""Backend stop readiness follows physical recovery across generated replacements."""

from __future__ import annotations

import asyncio

from hypothesis import example, given, settings
from hypothesis import strategies as st

from tests.core.retention.backendmodel import Step, play


class TestBackendModel:
    @settings(max_examples=40, deadline=None)
    @given(steps=st.lists(st.sampled_from(Step), min_size=1, max_size=25))
    @example(
        steps=[
            Step.STOP,
            Step.RECOVER,
            Step.RECHECK,
            Step.RELEASE,
            Step.STOP,
            Step.STALE,
            Step.DISPOSE,
            Step.STOP,
            Step.STALE,
        ]
    )
    @example(
        steps=[
            Step.REPORT_FAILS,
            Step.STOP,
            Step.RECHECK,
            Step.REPORT_RECOVERS,
            Step.RECHECK,
            Step.DISPOSE,
            Step.STOP,
        ]
    )
    def test_generated_stop_recovery_and_replacement(self, steps: list[Step]) -> None:
        asyncio.run(play(steps))
