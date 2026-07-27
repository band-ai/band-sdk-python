"""Guard against drift in ci.yml's hand-listed crewai test paths.

crewai lives in its own venv (`dev-crewai`), which cannot import the other
frameworks — so the crewai CI job cannot just run `pytest`, it names the test
files one by one. That list is the only thing standing between a crewai test and
never running anywhere: the default `test` job skips whatever needs the
crewai-only deps, and the crewai job only collects what the list names. A file
dropped from it goes silently uncovered, which is what happened to the crewai
cases in `test_capability_gating_e2e.py`.

So derive the set that *needs* the crewai venv from the tests themselves — via
the deps only that venv has — and assert the workflow covers every one.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

from tests.paths import REPO_ROOT

_CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
_TESTS_ROOT = REPO_ROOT / "tests"

# Import names of the distributions only `dev-crewai` installs. Kept as a map so
# the drift test below can prove it still matches pyproject: a new dev-crewai-only
# dep fails there rather than silently narrowing what this guard detects.
_CREWAI_ONLY_MODULES = {
    "crewai": "crewai",
    "nest-asyncio": "nest_asyncio",
    "pillow": "PIL",
}


def _needs_crewai_venv() -> re.Pattern[str]:
    """Match a real (non-TYPE_CHECKING) use of a crewai-venv-only module.

    Filename is not the signal — most crewai test files fake the package through
    ``sys.modules`` and run fine in `dev`, while the miss this guard exists for
    was a file with no "crewai" in its name at all.
    """
    names = "|".join(sorted(_CREWAI_ONLY_MODULES.values()))
    return re.compile(
        rf"^\s*(?:import|from)\s+(?:{names})\b|importorskip\(\s*[\"'](?:{names})",
        re.MULTILINE,
    )


def _crewai_only_distributions() -> set[str]:
    """Distributions in the `dev-crewai` extra that `dev` does not install."""
    extras = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["optional-dependencies"]

    def name(requirement: str) -> str:
        return re.split(r"[<>=!\[;]", requirement, maxsplit=1)[0].strip()

    return {name(r) for r in extras["dev-crewai"]} - {name(r) for r in extras["dev"]}


def _tests_needing_crewai_venv() -> set[Path]:
    """Repo-relative unit-test files that only the crewai venv can run.

    `tests/e2e/**` is excluded: those run from the e2e workflow's own crewai lane,
    not this job.
    """
    pattern = _needs_crewai_venv()
    return {
        path.relative_to(REPO_ROOT)
        for path in _TESTS_ROOT.rglob("test_*.py")
        if "e2e" not in path.relative_to(_TESTS_ROOT).parts
        and pattern.search(path.read_text(encoding="utf-8"))
    }


def _crewai_job_command() -> str:
    """The pytest invocation of ci.yml's crewai test step."""
    workflow = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["test-crewai"]["steps"]
    command = next(
        step["run"] for step in steps if step.get("name") == "Run crewai tests"
    )
    return command.replace("\\\n", " ")


def _crewai_job_paths() -> set[Path]:
    """The pytest targets of ci.yml's crewai test step."""
    return {
        Path(token)
        for token in _crewai_job_command().split()
        if token.startswith("tests/")
    }


def _covered_by(target: Path, listed: set[Path]) -> bool:
    """Whether ``target`` is named outright or sits under a listed directory.

    A directory target only covers what the step actually collects from it: the
    job narrows its directory targets with ``-k``, so a file that sits under one
    but does not match the filter runs nowhere despite looking listed.
    """
    if target in listed:
        return True
    return not _KEYWORD_FILTER.search(_crewai_job_command()) and any(
        parent in listed for parent in target.parents
    )


_KEYWORD_FILTER = re.compile(r"(?:^|\s)-k(?:\s|=)")


def test_crewai_job_runs_every_test_that_needs_its_venv() -> None:
    needed = _tests_needing_crewai_venv()
    listed = _crewai_job_paths()
    missing = sorted(str(path) for path in needed if not _covered_by(path, listed))
    assert not missing, (
        "these test files need a dev-crewai-only dependency but ci.yml's crewai "
        f"job never collects them, so they run nowhere: {missing}"
    )


def test_crewai_job_paths_all_exist() -> None:
    """The other drift direction: a listed path that was renamed or deleted is a
    silently empty target, since pytest is given the whole list at once."""
    stale = sorted(
        str(path) for path in _crewai_job_paths() if not (REPO_ROOT / path).exists()
    )
    assert not stale, f"ci.yml's crewai job lists paths that no longer exist: {stale}"


def test_crewai_only_dependency_set_matches_pyproject() -> None:
    """The detected module set is derived from a real dep list, not a guess."""
    assert _crewai_only_distributions() == set(_CREWAI_ONLY_MODULES), (
        "the dev-crewai-only dependencies changed; update _CREWAI_ONLY_MODULES so "
        "this guard still detects every test that needs that venv"
    )


def test_detection_finds_the_tests_that_only_the_crewai_venv_can_run() -> None:
    """Guard the guard: if the pattern drifts, the tests above pass empty.

    These are the whole set today — `phase3` for its nest_asyncio cases, the rest
    for importing crewai itself.
    """
    needed = _tests_needing_crewai_venv()
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
    lane that should stay strict, and only ci.yml's crewai job sends "1". A presence
    check reads that "0" as an opt-out — so the value has to be parsed as a boolean,
    or a cell asking to stay strict silently disables the fail-loud guard instead.
    """
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("BAND_ALLOW_MISSING_FRAMEWORKS", flag)

    from tests.framework_configs.sentinel import StrictnessSettings

    settings = StrictnessSettings()
    strict = settings.ci and not settings.band_allow_missing_frameworks
    assert strict is (flag != "1")
