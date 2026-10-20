from __future__ import annotations

from tests.framework_conformance import venv_job_coverage as vjc
from tests.framework_conformance.parlantcoverage.kit import (
    _PARLANT_ONLY_MODULES,
    _job_command,
    activity_command,
)


class TestParlantJobRunsEveryTestThatNeedsItsVenv:
    def test_parlant_job_runs_every_test_that_needs_its_venv(self) -> None:
        pattern = vjc.needs_venv_pattern(frozenset(_PARLANT_ONLY_MODULES.values()))
        needed = vjc.tests_needing_venv(pattern)
        command = _job_command()
        listed = vjc.job_paths(command)
        missing = sorted(
            str(path)
            for path in needed
            if not vjc.covered_by(path, listed, command)
            and not vjc.covered_by(
                path, vjc.job_paths(activity_command()), activity_command()
            )
        )
        assert not missing, (
            "these test files need a dev-parlant-only dependency but ci.yml's parlant "
            f"job never collects them, so they run nowhere: {missing}"
        )
