"""Historically slow tests run first so in-job xdist workers stay busy."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.paths import REPO_ROOT
from tests.testdurations import (
    DURATIONS_PATH_NAME,
    PATH_FLAG,
    slowest_first,
    write_durations,
)


def _collected_nodeids(result: pytest.RunResult) -> list[str]:
    return [
        line.strip()
        for line in result.stdout.str().splitlines()
        if "::" in line and not line.lstrip().startswith("=")
    ]


class TestDurationSchedule:
    def test_unknown_nodeids_sort_as_fastest(self) -> None:
        assert slowest_first(["new", "slow"], {"slow": 5.0}) == ["slow", "new"]

    def test_plugin_reorders_collection_from_the_durations_file(
        self, pytester: pytest.Pytester, tmp_path: Path
    ) -> None:
        pytester.syspathinsert(str(REPO_ROOT))
        pytester.makepyfile(
            test_order="""
            def test_fast():
                pass
            def test_slow():
                pass
            def test_mid():
                pass
            """
        )
        path = tmp_path / DURATIONS_PATH_NAME
        write_durations(
            path,
            {
                "test_order.py::test_fast": 0.1,
                "test_order.py::test_slow": 5.0,
                "test_order.py::test_mid": 1.0,
            },
        )
        result = pytester.runpytest(
            "-p",
            "tests.testdurations",
            f"{PATH_FLAG}={path}",
            "--collect-only",
            "-q",
        )
        assert result.ret == 0
        assert _collected_nodeids(result) == [
            "test_order.py::test_slow",
            "test_order.py::test_mid",
            "test_order.py::test_fast",
        ]
