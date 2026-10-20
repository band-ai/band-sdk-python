"""Human deliberation is excluded from the OpenCode compute budget."""

from __future__ import annotations

import pytest

from band.core.protocols import FAILURE_CODE_TIMEOUT
from tests.adapters.opencode.approvalclock import REPLY, pending_approval
from tests.adapters.opencode.helpers import COMPUTE_BUDGET_S
from tests.toolkit.clock import binary_clock_resolution

LONG_DELIBERATION_S = 2 * COMPUTE_BUDGET_S
WITHIN_BUDGET_S = 0.75 * COMPUTE_BUDGET_S


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestApprovalTiming:
    @pytest.mark.parametrize(
        ("deliberation", "computation"),
        [
            (LONG_DELIBERATION_S, WITHIN_BUDGET_S),
            (WITHIN_BUDGET_S, WITHIN_BUDGET_S),
        ],
        ids=[
            "manual-approval-pauses-timeout",
            "early-approval-preserves-resumed-budget",
        ],
    )
    async def test_human_time_is_excluded_from_the_compute_budget(
        self, deliberation: float, computation: float
    ) -> None:
        async with pending_approval() as turn:
            await turn.clock.elapse(deliberation)
            assert turn.aborted_sessions == []

            await turn.approve()
            await turn.clock.elapse(computation)
            assert turn.aborted_sessions == []

            await turn.finish()
            assert turn.complete
            assert turn.reply == REPLY
            assert turn.failure_codes == []

    async def test_resumed_computation_past_the_budget_times_out(self) -> None:
        async with pending_approval() as turn:
            await turn.clock.elapse(LONG_DELIBERATION_S)
            await turn.approve()
            await turn.clock.elapse(COMPUTE_BUDGET_S + 1)
            assert turn.aborted_sessions == ["sess-1"]

            await turn.finish()
            assert turn.failure_codes == [FAILURE_CODE_TIMEOUT]
