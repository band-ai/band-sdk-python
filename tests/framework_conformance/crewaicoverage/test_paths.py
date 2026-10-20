from __future__ import annotations

from tests.framework_conformance import venv_job_coverage as vjc
from tests.framework_conformance.crewaicoverage.kit import (
    _job_command,
    activity_command,
)
from tests.paths import REPO_ROOT


class TestCrewaiJobPathsAllExist:
    def test_crewai_job_paths_all_exist(self) -> None:
        """The other drift direction: a listed path that was renamed or deleted is a
        silently empty target, since pytest is given the whole list at once."""
        listed = vjc.job_paths(_job_command()) | vjc.job_paths(activity_command())
        stale = sorted(str(path) for path in listed if not (REPO_ROOT / path).exists())
        assert not stale, (
            f"ci.yml's crewai job lists paths that no longer exist: {stale}"
        )
