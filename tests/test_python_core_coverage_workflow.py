"""Structural contracts and guard-script behavior for python-core-coverage.yml."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from tests.paths import REPO_ROOT

WORKFLOW_PATH = REPO_ROOT / ".github/workflows/python-core-coverage.yml"


def load_workflow() -> dict[str, Any]:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _step(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    steps = workflow["jobs"]["coverage"]["steps"]
    return next(step for step in steps if step.get("name") == name)


def _pin_guard_script(run_text: str) -> str:
    """Isolate the pre-release guard invocation from the pin step's run text."""
    lines = run_text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("VERSION="))
    end = next(i for i, line in enumerate(lines) if 'echo "version=$version"' in line)
    return "\n".join(lines[start:end])


def _run_pin_guard(version: str) -> subprocess.CompletedProcess[str]:
    """Execute the real guard script from the workflow against a given version string."""
    workflow = load_workflow()
    run_text = _step(workflow, "Resolve pinned band-sdk-core version")["run"]
    core_tag_prefix = workflow["jobs"]["coverage"]["env"]["CORE_TAG_PREFIX"]
    script = f'version="{version}"\n{_pin_guard_script(run_text)}'
    bash = "bash"
    if sys.platform == "win32":
        bash = str(Path(os.environ["ProgramFiles"]) / "Git" / "bin" / "bash.exe")

    return subprocess.run(
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "CORE_TAG_PREFIX": core_tag_prefix},
    )


def test_pin_step_resolves_version_into_github_output() -> None:
    step = _step(load_workflow(), "Resolve pinned band-sdk-core version")
    assert step["id"] == "pin"
    assert "importlib.metadata.version('band-sdk-core')" in step["run"]
    assert '>> "$GITHUB_OUTPUT"' in step["run"]
    assert "version=$version" in step["run"]


def test_pin_guard_passes_for_a_stable_version() -> None:
    result = _run_pin_guard("2.4.0")
    assert result.returncode == 0, result.stderr


def test_pin_guard_rejects_dev_prerelease_versions() -> None:
    result = _run_pin_guard("2.4.0.dev3")
    assert result.returncode == 1
    assert "band-sdk-core==2.4.0.dev3 is a pre-release" in result.stderr
    assert "band-sdk-core-core-v2.4.0.dev3" in result.stderr


def test_pin_guard_rejects_cargo_style_dev_versions() -> None:
    result = _run_pin_guard("2.4.0-dev.3")
    assert result.returncode == 1
    assert "band-sdk-core==2.4.0-dev.3 is a pre-release" in result.stderr


def test_checkout_ref_matches_pin_step_output() -> None:
    step = _step(load_workflow(), "Checkout band-sdk-core at the pinned version")
    assert (
        step["with"]["ref"]
        == "${{ env.CORE_TAG_PREFIX }}${{ steps.pin.outputs.version }}"
    )


def test_prerelease_guard_runs_before_the_cross_repo_checkout() -> None:
    steps = load_workflow()["jobs"]["coverage"]["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Resolve pinned band-sdk-core version") < names.index(
        "Checkout band-sdk-core at the pinned version"
    )


def test_core_tag_prefix_is_a_single_source_of_truth() -> None:
    workflow = load_workflow()
    prefix = workflow["jobs"]["coverage"]["env"]["CORE_TAG_PREFIX"]
    assert prefix == "band-sdk-core-core-v"
    checkout_ref = _step(workflow, "Checkout band-sdk-core at the pinned version")[
        "with"
    ]["ref"]
    pin_run = _step(workflow, "Resolve pinned band-sdk-core version")["run"]
    assert "${{ env.CORE_TAG_PREFIX }}" in checkout_ref
    assert "os.environ['CORE_TAG_PREFIX']" in pin_run
    assert "band-sdk-core-core-v" not in checkout_ref
    assert "band-sdk-core-core-v" not in pin_run


def test_report_dir_is_a_single_source_of_truth() -> None:
    workflow = load_workflow()
    report_dir = workflow["jobs"]["coverage"]["env"]["REPORT_DIR"]
    summary_run = _step(workflow, "Write coverage summary")["run"]
    upload_paths = _step(workflow, "Upload coverage report")["with"]["path"]
    assert "$REPORT_DIR" in summary_run
    assert "${{ env.REPORT_DIR }}" in upload_paths
    assert report_dir not in summary_run
    assert report_dir not in upload_paths


def test_write_coverage_summary_has_no_working_directory() -> None:
    # band-sdk-core/ may not exist (the pin step's guard, or any earlier
    # failure, can skip its checkout) -- a nonexistent step cwd fails the
    # step outright before its own else-branch fallback can run.
    step = _step(load_workflow(), "Write coverage summary")
    assert "working-directory" not in step
    assert step["run"].strip().startswith('summary="band-sdk-core/')


def test_write_coverage_summary_falls_back_when_missing() -> None:
    run_text = _step(load_workflow(), "Write coverage summary")["run"]
    assert "No coverage summary produced" in run_text
    assert '>> "$GITHUB_STEP_SUMMARY"' in run_text
