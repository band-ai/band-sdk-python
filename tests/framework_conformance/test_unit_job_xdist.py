"""Unit CI parallelizes with xdist inside existing cells, not extra GitHub jobs."""

from __future__ import annotations

import tomllib

from tests.framework_conformance.venv_job_coverage import ci_jobs
from tests.paths import REPO_ROOT
from tests.xdistworker import CI_XDIST_FLAGS

# The four extra jobs' suite runs. Everything else that shells out to pytest
# (markdown-docs, E2E collect-only, the Strands coverage pin) stays serial.
UNIT_PYTEST_STEPS = frozenset(
    {
        ("test", "Run tests"),
        ("test-crewai", "Run crewai tests"),
        ("test-strands-nooa", "Run Strands and NOOA tests"),
        ("test-parlant", "Run parlant tests"),
    }
)


def _pytest_commands() -> tuple[tuple[str, str, str], ...]:
    found: list[tuple[str, str, str]] = []
    for job_name, job in ci_jobs().items():
        for step in job.get("steps", ()):
            run = step.get("run")
            if not isinstance(run, str) or "pytest" not in run:
                continue
            found.append((job_name, step["name"], run.replace("\\\n", " ")))
    return tuple(found)


class TestUnitJobXdist:
    def test_xdist_is_only_on_the_four_unit_suite_runs(self) -> None:
        seen: set[tuple[str, str]] = set()
        for job, step, command in _pytest_commands():
            key = (job, step)
            if key in UNIT_PYTEST_STEPS:
                seen.add(key)
                for flag in CI_XDIST_FLAGS:
                    assert flag in command, (job, step, flag)
                assert "--splits" not in command
                assert "matrix.shard" not in command
            else:
                for flag in CI_XDIST_FLAGS:
                    assert flag not in command, (job, step, flag)
        assert seen == UNIT_PYTEST_STEPS

    def test_xdist_is_not_in_addopts(self) -> None:
        pyproject = tomllib.loads(
            (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        addopts = pyproject["tool"]["pytest"]["ini_options"]["addopts"]
        # Local live runs and --pdb must stay single-process.
        xdist_options = ("-n", "--numprocesses", "--dist")
        assert not [opt for opt in addopts.split() if opt.startswith(xdist_options)], (
            addopts
        )
