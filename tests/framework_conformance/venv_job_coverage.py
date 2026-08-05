"""Shared machinery for the `test_<framework>_job_coverage.py` drift guards.

A framework whose deps conflict with the rest of `dev` (crewai, parlant) lives in
its own venv (`dev-crewai`, `dev-parlant`), which cannot import the other
frameworks — so its CI job cannot just run `pytest`, it names the test files one
by one. That list is the only thing standing between one of its tests and never
running anywhere: the default `test` job skips whatever needs the isolated venv's
deps, and the isolated job only collects what its list names. A file dropped from
it goes silently uncovered (this happened once, to the crewai cases in
`test_capability_gating_e2e.py`).

So each per-framework guard derives the set that *needs* its venv from the tests
themselves — via the deps only that venv has — and asserts the workflow covers
every one. This module holds the generic mechanics; `test_crewai_job_coverage.py`
and `test_parlant_job_coverage.py` supply the framework-specific module names and
job/step identifiers.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

from tests.paths import REPO_ROOT

CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
_TESTS_ROOT = REPO_ROOT / "tests"

# A `-k` filter anywhere in a job's command, e.g. `tests/framework_conformance/ -k crewai`.
_KEYWORD_FILTER = re.compile(r"(?:^|\s)-k(?:\s|=)")


def needs_venv_pattern(import_names: frozenset[str]) -> re.Pattern[str]:
    """Match a real (non-TYPE_CHECKING) use of any of ``import_names``.

    Filename is not the signal — most tests for an isolated framework fake the
    package through ``sys.modules`` and run fine in `dev`; the miss this guards
    against was a file with no framework name in it at all.
    """
    names = "|".join(sorted(import_names))
    return re.compile(
        rf"^\s*(?:import|from)\s+(?:{names})\b|importorskip\(\s*[\"'](?:{names})",
        re.MULTILINE,
    )


def only_distributions(extra: str, baseline_extra: str = "dev") -> set[str]:
    """Distributions in ``extra`` that ``baseline_extra`` does not install."""
    extras = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["optional-dependencies"]

    def name(requirement: str) -> str:
        return re.split(r"[<>=!\[;]", requirement, maxsplit=1)[0].strip()

    return {name(r) for r in extras[extra]} - {name(r) for r in extras[baseline_extra]}


def tests_needing_venv(pattern: re.Pattern[str]) -> set[Path]:
    """Repo-relative unit-test files that only a venv matching ``pattern`` can run.

    `tests/e2e/**` is excluded: those run from the e2e workflow's own lane, not a
    ci.yml job.
    """
    return {
        path.relative_to(REPO_ROOT)
        for path in _TESTS_ROOT.rglob("test_*.py")
        if "e2e" not in path.relative_to(_TESTS_ROOT).parts
        and pattern.search(path.read_text(encoding="utf-8"))
    }


def job_command(job_name: str, step_name: str) -> str:
    """The shell command of ``step_name`` in ci.yml's ``job_name`` job."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"][job_name]["steps"]
    command = next(step["run"] for step in steps if step.get("name") == step_name)
    return command.replace("\\\n", " ")


def job_paths(command: str) -> set[Path]:
    """The pytest targets named in ``command``."""
    return {Path(token) for token in command.split() if token.startswith("tests/")}


def covered_by(target: Path, listed: set[Path], command: str) -> bool:
    """Whether ``target`` is named outright or sits under a listed directory.

    A directory target only covers what the step actually collects from it: if
    ``command`` narrows with ``-k``, a file that sits under a listed directory but
    does not match the filter runs nowhere despite looking listed.
    """
    if target in listed:
        return True
    return not _KEYWORD_FILTER.search(command) and any(
        parent in listed for parent in target.parents
    )
