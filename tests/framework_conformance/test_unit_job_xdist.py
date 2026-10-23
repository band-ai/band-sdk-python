"""The unit CI jobs parallelize with xdist; only the main test job shards."""

from __future__ import annotations

import json

from tests.framework_conformance.venv_job_coverage import job_command
from tests.paths import REPO_ROOT

DURATIONS_PATH = REPO_ROOT / ".test_durations"
XDIST_FLAGS = ("-n auto", "--dist worksteal", "--maxprocesses=4")
EXTRA_PYTEST_STEPS = (
    ("test-crewai", "Run crewai tests"),
    ("test-strands-nooa", "Run Strands and NOOA tests"),
    ("test-parlant", "Run parlant tests"),
)


class TestUnitJobXdist:
    def test_main_test_job_runs_worksteal_and_two_way_least_duration_shards(
        self,
    ) -> None:
        command = job_command("test", "Run tests")
        for flag in XDIST_FLAGS:
            assert flag in command
        assert "--splits 2" in command
        assert "--group ${{ matrix.shard }}" in command
        assert "--splitting-algorithm least_duration" in command
        assert "--durations-path .test_durations" in command
        assert "--randomly-seed=" in command
        assert "--ignore=tests/e2e" in command
        assert "--ignore=tests/integration" in command

    def test_extra_jobs_use_xdist_without_splits(self) -> None:
        for job, step in EXTRA_PYTEST_STEPS:
            command = job_command(job, step)
            for flag in XDIST_FLAGS:
                assert flag in command, (job, flag)
            assert "--splits" not in command
            assert "--group" not in command

    def test_xdist_is_not_in_addopts(self) -> None:
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        # The pytest ini addopts line must stay "-v" only so local live runs
        # and pdb never inherit -n.
        assert 'addopts = "-v"' in pyproject

    def test_committed_durations_file_is_a_json_object(self) -> None:
        payload = json.loads(DURATIONS_PATH.read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
