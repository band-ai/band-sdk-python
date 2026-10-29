"""Generated permission waits and running tools keep each room generation's detail."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.runtime.permissionactivity.lifecycle import ActivityLifecycle, Step


class TestActivityLifecycle:
    @given(
        st.lists(
            st.tuples(st.sampled_from(Step), st.integers(0, 1), st.integers(0, 2)),
            min_size=1,
            max_size=60,
        )
    )
    @settings(max_examples=500, deadline=None)
    def test_detail_follows_waits_and_the_newest_running_tool(
        self, steps: list[tuple[Step, int, int]]
    ) -> None:
        lifecycle = ActivityLifecycle()
        for step, room, slot in steps:
            lifecycle.apply(step, room, slot)
        lifecycle.finish()
