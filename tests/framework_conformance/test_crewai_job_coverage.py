"""Guard against drift in ci.yml's hand-listed crewai test paths.

crewai lives in its own venv (`dev-crewai`), which cannot import the other
frameworks — so the crewai CI job cannot just run `pytest`, it names the test
files one by one. That list is the only thing standing between a crewai test and
never running anywhere: the default `test` job skips crewai tests (crewai absent)
and the crewai job only collects what the list names. A file dropped from it goes
silently uncovered, which is exactly what happened to the crewai cases in
`test_capability_gating_e2e.py`.

So derive the set that *needs* the crewai venv from the tests themselves and
assert the workflow covers every one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.paths import REPO_ROOT

_CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
_TESTS_ROOT = REPO_ROOT / "tests"

# A test file needs crewai installed if it imports crewai for real (directly or
# behind an availability probe). Filename is not the signal — the miss this guard
# exists for was a file with no "crewai" in its name.
_NEEDS_CREWAI = re.compile(
    r"^\s*(?:import|from)\s+crewai\b|importorskip\(\s*[\"']crewai", re.MULTILINE
)


def _crewai_test_files() -> set[Path]:
    """Repo-relative unit-test files that import crewai.

    `tests/e2e/**` is excluded: those run from the e2e workflow's own crewai lane,
    not this job.
    """
    return {
        path.relative_to(REPO_ROOT)
        for path in _TESTS_ROOT.rglob("test_*.py")
        if "e2e" not in path.relative_to(_TESTS_ROOT).parts
        and _NEEDS_CREWAI.search(path.read_text(encoding="utf-8"))
    }


def _crewai_job_paths() -> set[Path]:
    """The pytest targets of ci.yml's crewai test step."""
    workflow = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["test-crewai"]["steps"]
    command = next(
        step["run"] for step in steps if step.get("name") == "Run crewai tests"
    )
    return {
        Path(token)
        for token in command.replace("\\\n", " ").split()
        if token.startswith("tests/")
    }


def _covered_by(target: Path, listed: set[Path]) -> bool:
    """Whether ``target`` is named outright or sits under a listed directory."""
    return target in listed or any(
        parent in listed for parent in (target, *target.parents)
    )


def test_crewai_job_runs_every_test_that_imports_crewai() -> None:
    needed = _crewai_test_files()
    listed = _crewai_job_paths()
    missing = sorted(str(path) for path in needed if not _covered_by(path, listed))
    assert not missing, (
        "these test files import crewai but ci.yml's crewai job never collects "
        f"them, so they run nowhere: {missing}"
    )


def test_crewai_job_paths_all_exist() -> None:
    """The other drift direction: a listed path that was renamed or deleted is a
    silently empty target, since pytest is given the whole list at once."""
    stale = sorted(
        str(path) for path in _crewai_job_paths() if not (REPO_ROOT / path).exists()
    )
    assert not stale, f"ci.yml's crewai job lists paths that no longer exist: {stale}"


def test_crewai_detection_finds_the_real_importers() -> None:
    """Guard the guard: if the import regex drifts, the tests above pass empty.

    Most crewai test files fake the package through ``sys.modules`` and run in
    either venv (the default `test` job covers them). Only these two need crewai
    installed, which makes them the canary for the detection.
    """
    needed = _crewai_test_files()
    assert Path("tests/test_capability_gating_e2e.py") in needed
    assert Path("tests/integrations/test_crewai_flow_real_sdk.py") in needed


@pytest.mark.parametrize("flag", ["1", "0", ""])
def test_missing_framework_optout_is_parsed_as_a_boolean(
    flag: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``BAND_ALLOW_MISSING_FRAMEWORKS=0`` must keep strict mode ON.

    e2e.yml sets the flag per matrix cell and sends "0" for the lanes that should
    stay strict; a presence check would read that as an opt-out and disable the
    fail-loud framework-config guard for every lane.
    """
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("BAND_ALLOW_MISSING_FRAMEWORKS", flag)

    from tests.framework_configs.sentinel import StrictnessSettings

    settings = StrictnessSettings()
    strict = settings.ci and not settings.band_allow_missing_frameworks
    assert strict is (flag != "1")
