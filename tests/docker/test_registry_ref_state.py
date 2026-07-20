"""Tests for the fail-closed OCI reference probe used by kit publishing."""

from __future__ import annotations

import subprocess

import pytest
import yaml

from tests.loaders import load_script_module
from tests.paths import REPO_ROOT

probe = load_script_module("scripts/registry-ref-state.py", "registry_ref_state")


def completed(
    returncode: int, *, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["oras"], returncode, stdout, stderr)


def test_existing_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probe.subprocess, "run", lambda *args, **kwargs: completed(0, stdout="{}")
    )
    assert probe.registry_ref_state("registry.example/image:1.2.3") == "exists"


@pytest.mark.parametrize(
    "error",
    [
        "Error response from registry: manifest unknown",
        '{"errors":[{"code":"MANIFEST_UNKNOWN"}]}',
        "Error response from registry: name unknown",
        '{"errors":[{"code":"NAME_UNKNOWN"}]}',
        'Error response from registry: failed to find "registry.example/image:missing": registry.example/image:missing: not found',
    ],
)
def test_spec_defined_absence(monkeypatch: pytest.MonkeyPatch, error: str) -> None:
    monkeypatch.setattr(
        probe.subprocess, "run", lambda *args, **kwargs: completed(1, stderr=error)
    )
    assert probe.registry_ref_state("registry.example/image:missing") == "absent"


@pytest.mark.parametrize(
    "error",
    [
        "denied: requested access to the resource is denied",
        "unauthorized: authentication required",
        "too many requests",
        "dial tcp: connection refused",
        "context deadline exceeded",
        "resource not found while resolving a network endpoint",
    ],
)
def test_registry_and_transport_failures_fail_closed(
    monkeypatch: pytest.MonkeyPatch, error: str
) -> None:
    monkeypatch.setattr(
        probe.subprocess, "run", lambda *args, **kwargs: completed(1, stderr=error)
    )
    with pytest.raises(RuntimeError, match="registry probe failed"):
        probe.registry_ref_state("registry.example/image:1.2.3")


def test_missing_oras_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("oras")

    monkeypatch.setattr(probe.subprocess, "run", missing)
    with pytest.raises(RuntimeError, match="could not execute"):
        probe.registry_ref_state("registry.example/image:1.2.3")


def test_cli_reports_state_and_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(probe, "registry_ref_state", lambda *args, **kwargs: "absent")
    assert probe.main(["registry.example/image:missing"]) == 0
    assert capsys.readouterr().out == "absent\n"

    def failure(*args: object, **kwargs: object) -> str:
        raise RuntimeError("denied")

    monkeypatch.setattr(probe, "registry_ref_state", failure)
    assert probe.main(["registry.example/image:1.2.3"]) == 2
    assert "denied" in capsys.readouterr().err


def test_publish_workflow_uses_the_fail_closed_probe_for_both_artifacts() -> None:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/kit-publish.yml").read_text(encoding="utf-8")
    )
    runs = [
        step.get("run", "")
        for job in workflow["jobs"].values()
        if isinstance(job, dict)
        for step in job.get("steps", [])
    ]
    invocations = [run for run in runs if "registry-ref-state.py" in run]
    assert len(invocations) >= 2
    assert any("$IMAGE_NAME" in run or "${IMAGE_NAME}" in run for run in invocations)
    assert any("$KIT_NAME" in run or "${KIT_NAME}" in run for run in invocations)
