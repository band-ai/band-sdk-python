"""Human deliberation is excluded from the OpenCode compute budget."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest

from tests.adapters.opencode.approvalclock import REPLY, ApprovalTurn
from tests.toolkit.clock import binary_clock_resolution


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestApprovalTiming:
    @pytest.mark.parametrize(
        ("deliberation", "computation"),
        [(16.0, 6.0), (6.0, 6.0)],
        ids=[
            "manual-approval-pauses-timeout",
            "early-approval-preserves-resumed-budget",
        ],
    )
    async def test_human_time_is_excluded_from_the_compute_budget(
        self,
        approval_turn: Callable[[], AbstractAsyncContextManager[ApprovalTurn]],
        deliberation: float,
        computation: float,
    ) -> None:
        async with approval_turn() as turn:
            await turn.clock.elapse(deliberation)
            assert turn.aborted_sessions == []

            await turn.approve()
            await turn.clock.elapse(computation)
            assert turn.aborted_sessions == []

            await turn.finish()
            assert turn.complete
            assert turn.reply == REPLY
            assert turn.failures == []
