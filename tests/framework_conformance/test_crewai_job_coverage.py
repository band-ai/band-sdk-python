"""Guard against drift in ci.yml's hand-listed crewai test paths.

See `venv_job_coverage`'s module docstring for the general shape of this guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.framework_conformance import venv_job_coverage as vjc
from tests.paths import REPO_ROOT

# Import names of the distributions only `dev-crewai` installs. Kept as a map so
# the drift test below can prove it still matches pyproject: a new dev-crewai-only
# dep fails there rather than silently narrowing what this guard detects.
_CREWAI_ONLY_MODULES = {
    "crewai": "crewai",
    "nest-asyncio": "nest_asyncio",
    "pillow": "PIL",
}


def _job_command() -> str:
    return vjc.job_command("test-crewai", "Run crewai tests")


def test_crewai_job_runs_every_test_that_needs_its_venv() -> None:
    pattern = vjc.needs_venv_pattern(frozenset(_CREWAI_ONLY_MODULES.values()))
    needed = vjc.tests_needing_venv(pattern)
    command = _job_command()
    listed = vjc.job_paths(command)
    missing = sorted(
        str(path) for path in needed if not vjc.covered_by(path, listed, command)
    )
    assert not missing, (
        "these test files need a dev-crewai-only dependency but ci.yml's crewai "
        f"job never collects them, so they run nowhere: {missing}"
    )


def test_crewai_job_paths_all_exist() -> None:
    """The other drift direction: a listed path that was renamed or deleted is a
    silently empty target, since pytest is given the whole list at once."""
    listed = vjc.job_paths(_job_command())
    stale = sorted(str(path) for path in listed if not (REPO_ROOT / path).exists())
    assert not stale, f"ci.yml's crewai job lists paths that no longer exist: {stale}"


def test_crewai_only_dependency_set_matches_pyproject() -> None:
    """The detected module set is derived from a real dep list, not a guess."""
    assert vjc.only_distributions("dev-crewai") == set(_CREWAI_ONLY_MODULES), (
        "the dev-crewai-only dependencies changed; update _CREWAI_ONLY_MODULES so "
        "this guard still detects every test that needs that venv"
    )


def test_detection_finds_the_tests_that_only_the_crewai_venv_can_run() -> None:
    """Guard the guard: if the pattern drifts, the tests above pass empty.

    These are the whole set today — `phase3` for its nest_asyncio cases, the rest
    for importing crewai itself.
    """
    pattern = vjc.needs_venv_pattern(frozenset(_CREWAI_ONLY_MODULES.values()))
    needed = vjc.tests_needing_venv(pattern)
    assert needed == {
        Path("tests/adapters/test_crewai_flow_phase3.py"),
        Path("tests/integrations/test_crewai_flow_real_sdk.py"),
        Path("tests/integrations/test_crewai_real_tools.py"),
        Path("tests/test_capability_gating_e2e.py"),
    }


@pytest.mark.parametrize("flag", ["1", "0", ""])
def test_missing_framework_optout_is_parsed_as_a_boolean(
    flag: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``BAND_ALLOW_MISSING_FRAMEWORKS=0`` must keep strict mode ON.

    The flag is set explicitly rather than conditionally: e2e.yml sends "0" for every
    lane that should stay strict, and only ci.yml's crewai/parlant jobs send "1". A
    presence check reads that "0" as an opt-out — so the value has to be parsed as a
    boolean, or a cell asking to stay strict silently disables the fail-loud guard
    instead.
    """
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("BAND_ALLOW_MISSING_FRAMEWORKS", flag)

    from tests.framework_configs.sentinel import StrictnessSettings

    settings = StrictnessSettings()
    strict = settings.ci and not settings.band_allow_missing_frameworks
    assert strict is (flag != "1")
