from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.runtime.permissionactivity.lifecycle import PermissionLifecycle, Step


class TestPermissionLifecycle:
    @given(
        st.lists(
            st.tuples(st.sampled_from(Step), st.integers(0, 1), st.integers(0, 2)),
            min_size=1,
            max_size=60,
        )
    )
    @settings(max_examples=100, deadline=None)
    def test_owners_follow_the_original_generation(
        self, steps: list[tuple[Step, int, int]]
    ) -> None:
        lifecycle = PermissionLifecycle()
        for step, room, slot in steps:
            lifecycle.apply(step, room, slot)
        lifecycle.finish()
