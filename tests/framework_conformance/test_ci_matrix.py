"""Guard the PR, release, scheduled, manual, and merge-queue test matrices."""

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
FULL_WINDOWS = re.compile(
    r"\$\{\{ github\.event_name != '([a-z_]+)' \|\| github\.head_ref == '([^']+)' \}\}"
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


def full_windows(expression: str, event: str, head_ref: str) -> bool:
    match = FULL_WINDOWS.fullmatch(expression)
    assert match, f"unrecognised full-windows expression: {expression}"
    slim_event, release_branch = match.groups()
    return event != slim_event or head_ref == release_branch


def resolved_legs(matrix: dict[str, Any], event: str, head_ref: str = "") -> set[Leg]:
    """The (os, python) legs GitHub runs for this event, after `exclude`.

    An exclude row removes every combination whose values match all of its keys.
    """
    axes = {
        "os": matrix["os"],
        "python-version": matrix["python-version"],
        "run-for": [run_for(value, event) for value in matrix["run-for"]],
        "full-windows": [
            full_windows(value, event, head_ref) for value in matrix["full-windows"]
        ],
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


class TestCiMatrix:
    def test_every_suite_scopes_its_legs(self) -> None:
        assert suite_matrices().keys() == {
            "test",
            "test-crewai",
            "test-strands-nooa",
            "test-parlant",
        }

    @pytest.mark.parametrize("suite", sorted(suite_matrices()))
    def test_every_suite_can_run_on_scheduled_and_manual_events(
        self, suite: str
    ) -> None:
        jobs = ci_jobs()
        ready = jobs["lint"]["if"]
        assert jobs[suite]["if"] in {
            ready,
            f"github.event_name != 'merge_group' && ({ready})",
        }

    @pytest.mark.parametrize("suite", sorted(suite_matrices()))
    def test_merge_queue_runs_only_ubuntu_with_python_3_12(self, suite: str) -> None:
        assert resolved_legs(suite_matrices()[suite], "merge_group") == {QUEUE_LEG}

    @pytest.mark.parametrize("suite", sorted(suite_matrices()))
    def test_normal_pull_requests_run_linux_and_only_the_newest_python_on_windows(
        self, suite: str
    ) -> None:
        matrix = suite_matrices()[suite]
        versions = matrix["python-version"]
        newest = max(versions, key=lambda version: tuple(map(int, version.split("."))))
        assert resolved_legs(matrix, "pull_request", "feat/example") == {
            *(("ubuntu-latest", version) for version in versions),
            ("windows-latest", newest),
        }

    @pytest.mark.parametrize("suite", sorted(suite_matrices()))
    @pytest.mark.parametrize(
        ("event", "head_ref"),
        [
            ("pull_request", "release-please--branches--main"),
            ("schedule", ""),
            ("workflow_dispatch", ""),
        ],
    )
    def test_release_scheduled_and_manual_runs_cover_the_full_grid(
        self, suite: str, event: str, head_ref: str
    ) -> None:
        matrix = suite_matrices()[suite]
        assert resolved_legs(matrix, event, head_ref) == full_grid(matrix)

    def test_only_the_main_test_job_defines_shards(self) -> None:
        jobs = ci_jobs()
        test_matrix = jobs["test"]["strategy"]["matrix"]
        assert test_matrix["shard"] == [1, 2]
        unsharded = {key: value for key, value in test_matrix.items() if key != "shard"}
        for name in suite_matrices():
            if name == "test":
                continue
            extra = jobs[name]["strategy"]["matrix"]
            assert "shard" not in extra
            assert extra == unsharded
