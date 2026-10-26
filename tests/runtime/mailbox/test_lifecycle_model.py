"""Generated mailbox operations preserve FIFO and worker restart behavior."""

from __future__ import annotations

import asyncio

from hypothesis import example, given, settings

from tests.runtime.mailbox.lifecycle import (
    SEQUENCE_GUARD_S,
    Step,
    play,
    sequences,
)


class TestMailboxLifecycleModel:
    @given(steps=sequences)
    @example(
        steps=[
            Step.CAST,
            Step.SPAWN,
            Step.CANCEL_QUEUED_ASK,
            Step.CANCEL_HANDOFF_ASK,
            Step.CANCEL_HANDOFF_SPAWN,
            Step.CLOSE,
            Step.ASK,
            Step.REOPEN,
        ]
    )
    @settings(max_examples=50, deadline=None)
    def test_generated_sequences_preserve_fifo_and_reopen(
        self, steps: list[Step]
    ) -> None:
        asyncio.run(asyncio.wait_for(play(steps), timeout=SEQUENCE_GUARD_S))
