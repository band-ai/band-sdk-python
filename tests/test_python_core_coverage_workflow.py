"""Structural contracts for python-core-coverage.yml."""

from __future__ import annotations

from typing import Any

import yaml

from tests.paths import REPO_ROOT

WORKFLOW_PATH = REPO_ROOT / ".github/workflows/python-core-coverage.yml"


def load_workflow() -> dict[str, Any]:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _step(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    steps = workflow["jobs"]["coverage"]["steps"]
    return next(step for step in steps if step.get("name") == name)


def test_pin_step_resolves_version_into_github_output() -> None:
    step = _step(load_workflow(), "Resolve pinned band-sdk-core version")
    assert step["id"] == "pin"
    assert "importlib.metadata.version('band-sdk-core')" in step["run"]
    assert '>> "$GITHUB_OUTPUT"' in step["run"]
    assert "version=$version" in step["run"]


def test_pin_step_rejects_prerelease_versions_before_checkout() -> None:
    step = _step(load_workflow(), "Resolve pinned band-sdk-core version")
    assert "is_prerelease" in step["run"]
    assert "packaging.version" in step["run"]


def test_checkout_ref_matches_pin_step_output() -> None:
    step = _step(load_workflow(), "Checkout band-sdk-core at the pinned version")
    assert step["with"]["ref"] == "band-sdk-core-core-v${{ steps.pin.outputs.version }}"


def test_prerelease_guard_runs_before_the_cross_repo_checkout() -> None:
    steps = load_workflow()["jobs"]["coverage"]["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Resolve pinned band-sdk-core version") < names.index(
        "Checkout band-sdk-core at the pinned version"
    )
