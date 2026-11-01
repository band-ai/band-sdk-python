"""Reporting depends only on current authoritative origin deadlines."""

from __future__ import annotations

import pytest

from band.runtime.working_state import project_working


class TestWorkingProjection:
    @pytest.mark.parametrize(
        ("deadlines", "now", "expected"),
        [
            ((), 5, False),
            ((None,), 5, True),
            ((5,), 5, None),
            ((4, 6), 5, True),
            ((4,), 5, None),
            ((4, 5), 5, None),
        ],
        ids=[
            "no-authority",
            "unbounded",
            "at-cap",
            "fresh-foreground",
            "foreground-completed",
            "all-capped",
        ],
    )
    def test_projects_current_authority_without_event_history(
        self, deadlines: tuple[float | None, ...], now: float, expected: bool | None
    ) -> None:
        assert project_working(deadlines, now) is expected
