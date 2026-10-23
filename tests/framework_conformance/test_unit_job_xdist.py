"""The unit CI jobs parallelize with xdist inside the existing matrix cells."""

from __future__ import annotations

from tests.framework_conformance.venv_job_coverage import job_command
from tests.paths import REPO_ROOT
from tests.xdistworker import CI_XDIST_FLAGS

UNIT_PYTEST_STEPS = (
    ("test", "Run tests"),
    ("test-crewai", "Run crewai tests"),
    ("test-strands-nooa", "Run Strands and NOOA tests"),
    ("test-parlant", "Run parlant tests"),
)
SERIAL_PYTEST_STEPS = (
    ("test", "Collect baseline E2E in the core environment"),
    ("test", "Run markdown doc tests"),
    ("test-crewai", "Collect baseline E2E in the CrewAI environment"),
    ("test-strands-nooa", "Collect baseline E2E in the Strands and NOOA environment"),
    ("test-strands-nooa", "Verify selected Strands and NOOA coverage"),
    ("test-strands-nooa", "Run NOOA markdown examples"),
    ("test-parlant", "Collect baseline E2E in the Parlant environment"),
)


class TestUnitJobXdist:
    def test_every_unit_job_runs_worksteal_inside_the_existing_cell(self) -> None:
        for job, step in UNIT_PYTEST_STEPS:
            command = job_command(job, step)
            for flag in CI_XDIST_FLAGS:
                assert flag in command, (job, flag)
            assert "--splits" not in command
            assert "matrix.shard" not in command

    def test_collect_only_and_markdown_docs_stay_serial(self) -> None:
        for job, step in SERIAL_PYTEST_STEPS:
            command = job_command(job, step)
            for flag in CI_XDIST_FLAGS:
                assert flag not in command, (job, step, flag)

    def test_main_test_job_ignores_live_suites(self) -> None:
        command = job_command("test", "Run tests")
        assert "--ignore=tests/e2e" in command
        assert "--ignore=tests/integration" in command

    def test_xdist_is_not_in_addopts(self) -> None:
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        # The pytest ini addopts line must stay "-v" only so local live runs
        # and pdb never inherit -n.
        assert 'addopts = "-v"' in pyproject
