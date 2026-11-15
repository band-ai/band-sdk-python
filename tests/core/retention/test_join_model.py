"""Bounded dependency graphs preserve only current transitive callers."""

from __future__ import annotations

import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.core.retention.joinmodel import play


class TestJoinModel:
    @settings(max_examples=30, deadline=None)
    @given(
        parents=st.lists(st.integers(min_value=0, max_value=6), max_size=6),
        departed=st.sets(st.integers(min_value=1, max_value=6)),
        fail_close=st.booleans(),
    )
    def test_active_dependents(
        self, parents: list[int], departed: set[int], fail_close: bool
    ) -> None:
        asyncio.run(
            play(
                parents,
                {index for index in departed if index <= len(parents)},
                fail_close,
            )
        )
