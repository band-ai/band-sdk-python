"""Guard: the merge queue runs each test suite on one leg, pull requests on the full grid.

Each suite's matrix carries a `run-for` key that is `queue` in the merge queue and
`pr` otherwise, and `exclude` rows that drop every queue leg but Ubuntu with
Python 3.12. A Python version or OS added without its exclude row would quietly
grow the queue again.
"""

from __future__ import annotations

import itertools
import re
from typing import Any

import pytest

from tests.framework_conformance.venv_job_coverage import ci_jobs

QUEUE_LEG = ("ubuntu-latest", "3.12")
# `${{ github.event_name == '<event>' && '<then>' || '<else>' }}`
RUN_FOR = re.compile(
    r"\$\{\{ github\.event_name == '([a-z_]+)' && '([a-z]+)' \|\| '([a-z]+)' \}\}"
)
Leg = tuple[str, str]


def suite_matrices() -> dict[str, dict[str, Any]]:
    """The matrix of every ci.yml job that scopes its legs with `run-for`."""
    return {
        name: job["strategy"]["matrix"]
        for name, job in ci_jobs().items()
        if "run-for" in job.get("strategy", {}).get("matrix", {})
    }


def run_for(expression: str, event: str) -> str:
    """The value the `run-for` expression takes for one triggering event."""
    match = RUN_FOR.fullmatch(expression)
    assert match, f"unrecognised run-for expression: {expression}"
    tested_event, then, otherwise = match.groups()
    return then if event == tested_event else otherwise


def resolved_legs(matrix: dict[str, Any], event: str) -> set[Leg]:
    """The (os, python) legs GitHub runs for this event, after `exclude`.

    An exclude row removes every combination whose values match all of its keys.
    """
    axes = {
        "os": matrix["os"],
        "python-version": matrix["python-version"],
        "run-for": [run_for(value, event) for value in matrix["run-for"]],
    }
    combinations = [
        dict(zip(axes, values, strict=True))
        for values in itertools.product(*axes.values())
    ]
    return {
        (combo["os"], combo["python-version"])
        for combo in combinations
        if not any(
            all(combo[key] == value for key, value in row.items())
            for row in matrix.get("exclude", [])
        )
    }


def full_grid(matrix: dict[str, Any]) -> set[Leg]:
    return set(itertools.product(matrix["os"], matrix["python-version"]))


class TestCiQueueMatrix:
    def test_the_slim_suites_scope_their_legs(self) -> None:
        assert {"test", "test-crewai", "test-parlant"} <= suite_matrices().keys()

    @pytest.mark.parametrize("suite", sorted(suite_matrices()))
    def test_merge_queue_runs_only_ubuntu_with_python_3_12(self, suite: str) -> None:
        assert resolved_legs(suite_matrices()[suite], "merge_group") == {QUEUE_LEG}

    @pytest.mark.parametrize("suite", sorted(suite_matrices()))
    def test_pull_requests_run_the_full_grid(self, suite: str) -> None:
        matrix = suite_matrices()[suite]
        assert resolved_legs(matrix, "pull_request") == full_grid(matrix)
