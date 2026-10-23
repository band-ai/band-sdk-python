"""The unit CI jobs parallelize with xdist inside the existing matrix cells."""

from __future__ import annotations

from tests.framework_conformance.venv_job_coverage import job_command
from tests.paths import REPO_ROOT

XDIST_FLAGS = ("-n auto", "--dist worksteal", "--maxprocesses=4")
UNIT_PYTEST_STEPS = (
    ("test", "Run tests"),
    ("test-crewai", "Run crewai tests"),
    ("test-strands-nooa", "Run Strands and NOOA tests"),
    ("test-parlant", "Run parlant tests"),
)


class TestUnitJobXdist:
    def test_every_unit_job_runs_worksteal_inside_the_existing_cell(self) -> None:
        for job, step in UNIT_PYTEST_STEPS:
            command = job_command(job, step)
            for flag in XDIST_FLAGS:
                assert flag in command, (job, flag)
            assert "--splits" not in command
            assert "matrix.shard" not in command

    def test_main_test_job_ignores_live_suites(self) -> None:
        command = job_command("test", "Run tests")
        assert "--ignore=tests/e2e" in command
        assert "--ignore=tests/integration" in command

    def test_xdist_is_not_in_addopts(self) -> None:
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        # The pytest ini addopts line must stay "-v" only so local live runs
        # and pdb never inherit -n.
        assert 'addopts = "-v"' in pyproject
