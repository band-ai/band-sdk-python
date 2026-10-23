"""Historically slow tests run first so in-job xdist workers stay busy."""

from __future__ import annotations

from tests.testdurations import slowest_first


class TestDurationSchedule:
    def test_slower_tests_move_ahead_of_faster_ones(self) -> None:
        nodeids = ["fast", "slow", "mid"]
        durations = {"fast": 0.1, "slow": 5.0, "mid": 1.0}
        assert slowest_first(nodeids, durations) == ["slow", "mid", "fast"]

    def test_equal_durations_keep_incoming_order(self) -> None:
        nodeids = ["c", "a", "b"]
        durations = {"c": 1.0, "a": 1.0, "b": 1.0}
        assert slowest_first(nodeids, durations) == ["c", "a", "b"]

    def test_unknown_nodeids_sort_as_fastest(self) -> None:
        nodeids = ["new", "slow"]
        durations = {"slow": 5.0}
        assert slowest_first(nodeids, durations) == ["slow", "new"]
