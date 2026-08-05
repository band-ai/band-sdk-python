"""Guard against drift in ci.yml's hand-listed parlant test paths.

parlant lives in its own venv (`dev-parlant`), which cannot import the other
frameworks — so the parlant CI job cannot just run `pytest`, it names the test
files one by one. That list is the only thing standing between a parlant test and
never running anywhere: the default `test` job skips whatever needs the
parlant-only deps, and the parlant job only collects what the list names. A file
dropped from it goes silently uncovered.

So derive the set that *needs* the parlant venv from the tests themselves — via
the deps only that venv has — and assert the workflow covers every one.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

from tests.paths import REPO_ROOT

_CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
_TESTS_ROOT = REPO_ROOT / "tests"

# Import names of the distributions only `dev-parlant` installs. Kept as a map so
# the drift test below can prove it still matches pyproject: a new dev-parlant-only
# dep fails there rather than silently narrowing what this guard detects.
_PARLANT_ONLY_MODULES = {
    "parlant": "parlant",
    "werkzeug": "werkzeug",
}


def _needs_parlant_venv() -> re.Pattern[str]:
    """Match a real (non-TYPE_CHECKING) use of a parlant-venv-only module.

    Filename is not the signal — most parlant test files fake the package through
    ``sys.modules`` and run fine in `dev`.
    """
    names = "|".join(sorted(_PARLANT_ONLY_MODULES.values()))
    return re.compile(
        rf"^\s*(?:import|from)\s+(?:{names})\b|importorskip\(\s*[\"'](?:{names})",
        re.MULTILINE,
    )


def _parlant_only_distributions() -> set[str]:
    """Distributions in the `dev-parlant` extra that `dev` does not install."""
    extras = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["optional-dependencies"]

    def name(requirement: str) -> str:
        return re.split(r"[<>=!\[;]", requirement, maxsplit=1)[0].strip()

    return {name(r) for r in extras["dev-parlant"]} - {name(r) for r in extras["dev"]}


def _tests_needing_parlant_venv() -> set[Path]:
    """Repo-relative unit-test files that only the parlant venv can run.

    `tests/e2e/**` is excluded: those run from the e2e workflow's own parlant lane,
    not this job.
    """
    pattern = _needs_parlant_venv()
    return {
        path.relative_to(REPO_ROOT)
        for path in _TESTS_ROOT.rglob("test_*.py")
        if "e2e" not in path.relative_to(_TESTS_ROOT).parts
        and pattern.search(path.read_text(encoding="utf-8"))
    }


def _parlant_job_command() -> str:
    """The pytest invocation of ci.yml's parlant test step."""
    workflow = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["test-parlant"]["steps"]
    command = next(
        step["run"] for step in steps if step.get("name") == "Run parlant tests"
    )
    return command.replace("\\\n", " ")


def _parlant_job_paths() -> set[Path]:
    """The pytest targets of ci.yml's parlant test step."""
    return {
        Path(token)
        for token in _parlant_job_command().split()
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
    return not _KEYWORD_FILTER.search(_parlant_job_command()) and any(
        parent in listed for parent in target.parents
    )


_KEYWORD_FILTER = re.compile(r"(?:^|\s)-k(?:\s|=)")


def test_parlant_job_runs_every_test_that_needs_its_venv() -> None:
    needed = _tests_needing_parlant_venv()
    listed = _parlant_job_paths()
    missing = sorted(str(path) for path in needed if not _covered_by(path, listed))
    assert not missing, (
        "these test files need a dev-parlant-only dependency but ci.yml's parlant "
        f"job never collects them, so they run nowhere: {missing}"
    )


def test_parlant_job_paths_all_exist() -> None:
    """The other drift direction: a listed path that was renamed or deleted is a
    silently empty target, since pytest is given the whole list at once."""
    stale = sorted(
        str(path) for path in _parlant_job_paths() if not (REPO_ROOT / path).exists()
    )
    assert not stale, f"ci.yml's parlant job lists paths that no longer exist: {stale}"


def test_parlant_only_dependency_set_matches_pyproject() -> None:
    """The detected module set is derived from a real dep list, not a guess."""
    assert _parlant_only_distributions() == set(_PARLANT_ONLY_MODULES), (
        "the dev-parlant-only dependencies changed; update _PARLANT_ONLY_MODULES so "
        "this guard still detects every test that needs that venv"
    )


def test_detection_finds_the_tests_that_only_the_parlant_venv_can_run() -> None:
    """Guard the guard: if the pattern drifts, the tests above pass empty.

    ``BAND_ALLOW_MISSING_FRAMEWORKS`` boolean-parsing is shared, non-parlant-specific
    machinery — covered once in ``test_crewai_job_coverage.py``, not duplicated here.
    """
    needed = _tests_needing_parlant_venv()
    assert needed == {
        Path("tests/integrations/parlant/test_tools.py"),
    }
