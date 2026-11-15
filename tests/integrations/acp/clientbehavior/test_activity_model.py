"""Generated ACP partial updates preserve running operations and session isolation."""

from __future__ import annotations

from hypothesis import example, given, settings
from hypothesis import strategies as st

from band.integrations.acp.types import ToolStatus
from tests.integrations.acp.clientbehavior.activitymodel import (
    Step,
    play_steps,
)


class TestActivityModel:
    @given(
        st.lists(
            st.tuples(
                st.sampled_from(Step),
                st.integers(0, 1),
                st.integers(0, 1),
                st.sampled_from([None, *ToolStatus]),
            ),
            min_size=1,
            max_size=60,
        )
    )
    @example(
        steps=[
            (Step.START, 0, 0, ToolStatus.IN_PROGRESS),
            (Step.START, 0, 1, ToolStatus.PENDING),
            (Step.UPDATE, 0, 0, ToolStatus.COMPLETED),
            (Step.UPDATE, 0, 1, ToolStatus.IN_PROGRESS),
            (Step.UPDATE, 0, 1, None),
            (Step.UPDATE, 0, 1, ToolStatus.FAILED),
            (Step.UPDATE, 0, 1, ToolStatus.IN_PROGRESS),
            (Step.RESET, 0, 0, None),
            (Step.UPDATE, 0, 1, ToolStatus.IN_PROGRESS),
        ]
    )
    @settings(max_examples=300, deadline=None)
    async def test_partial_statuses_follow_the_running_history(
        self, steps: list[tuple[Step, int, int, ToolStatus | None]]
    ) -> None:
        await play_steps(steps)
