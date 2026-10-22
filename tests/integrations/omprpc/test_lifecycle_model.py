"""Owned process trees and admitted turns survive generated host interleavings."""

from __future__ import annotations

import asyncio
import sys

import pytest
from hypothesis import Phase, example, given, settings

from tests.integrations.omprpc.lifecycle import (
    STEP_GUARD_S,
    Step,
    rpc_lifecycle,
    sequences,
)


async def play(steps: list[Step], *, detached: bool) -> None:
    async with rpc_lifecycle(detached=detached) as session:
        for step in steps:
            async with asyncio.timeout(STEP_GUARD_S):
                await session.take(step)
        async with asyncio.timeout(STEP_GUARD_S):
            await session.assert_custody_survives()


class TestRpcLifecycleModel:
    @pytest.mark.parametrize("detached", [False, True], ids=["session", "detached"])
    # The full Hypothesis workload can exceed two minutes on Windows.
    # Each generated step retains its separate, shorter hang guard.
    @pytest.mark.timeout(600)
    @settings(
        # Explicit scenarios still run everywhere; generated Windows peers are costly.
        max_examples=8 if sys.platform == "win32" else 20,
        deadline=None,
        phases=(Phase.explicit, Phase.reuse, Phase.generate),
    )
    @given(steps=sequences)
    @example(steps=[Step.PROMPT, Step.CANCEL_QUEUED, Step.SETTLE])
    @example(steps=[Step.PROMPT, Step.CRASH, Step.PROMPT, Step.CANCEL_OWNER])
    @example(steps=[Step.CANCEL_START_RESTART, Step.PROMPT, Step.CLOSE])
    @example(steps=[Step.CANCEL_CLOSE_RESTART, Step.PROMPT, Step.SETTLE])
    @example(
        steps=[
            Step.CANCEL_CLOSE_RESTART,
            Step.CANCEL_OWNER,
            Step.CANCEL_CLOSE_RESTART,
        ]
    )
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
