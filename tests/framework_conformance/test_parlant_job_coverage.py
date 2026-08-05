"""Guard against drift in ci.yml's hand-listed parlant test paths.

See `venv_job_coverage`'s module docstring for the general shape of this guard.
``BAND_ALLOW_MISSING_FRAMEWORKS`` boolean-parsing is shared, non-parlant-specific
machinery — covered once in ``test_crewai_job_coverage.py``, not duplicated here.
"""

from __future__ import annotations

from pathlib import Path

from tests.framework_conformance import venv_job_coverage as vjc
from tests.paths import REPO_ROOT

# Import names of the distributions only `dev-parlant` installs. Kept as a map so
# the drift test below can prove it still matches pyproject: a new dev-parlant-only
# dep fails there rather than silently narrowing what this guard detects.
_PARLANT_ONLY_MODULES = {
    "parlant": "parlant",
    "werkzeug": "werkzeug",
}


def _job_command() -> str:
    return vjc.job_command("test-parlant", "Run parlant tests")


def test_parlant_job_runs_every_test_that_needs_its_venv() -> None:
    pattern = vjc.needs_venv_pattern(frozenset(_PARLANT_ONLY_MODULES.values()))
    needed = vjc.tests_needing_venv(pattern)
    command = _job_command()
    listed = vjc.job_paths(command)
    missing = sorted(
        str(path) for path in needed if not vjc.covered_by(path, listed, command)
    )
    assert not missing, (
        "these test files need a dev-parlant-only dependency but ci.yml's parlant "
        f"job never collects them, so they run nowhere: {missing}"
    )


def test_parlant_job_paths_all_exist() -> None:
    """The other drift direction: a listed path that was renamed or deleted is a
    silently empty target, since pytest is given the whole list at once."""
    listed = vjc.job_paths(_job_command())
    stale = sorted(str(path) for path in listed if not (REPO_ROOT / path).exists())
    assert not stale, f"ci.yml's parlant job lists paths that no longer exist: {stale}"


def test_parlant_only_dependency_set_matches_pyproject() -> None:
    """The detected module set is derived from a real dep list, not a guess."""
    assert vjc.only_distributions("dev-parlant") == set(_PARLANT_ONLY_MODULES), (
        "the dev-parlant-only dependencies changed; update _PARLANT_ONLY_MODULES so "
        "this guard still detects every test that needs that venv"
    )


def test_detection_finds_the_tests_that_only_the_parlant_venv_can_run() -> None:
    """Guard the guard: if the pattern drifts, the tests above pass empty."""
    pattern = vjc.needs_venv_pattern(frozenset(_PARLANT_ONLY_MODULES.values()))
    needed = vjc.tests_needing_venv(pattern)
    assert needed == {
        Path("tests/integrations/parlant/test_tools.py"),
    }
