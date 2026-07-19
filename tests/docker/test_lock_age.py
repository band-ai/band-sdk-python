"""Unit tests for scripts/check-lock-age.py (the supply-chain quarantine gate).

The gate refuses to publish an image whose lock contains any artifact younger
than the quarantine window, reading the PEP 700 upload-times uv records in
``uv.lock``. These tests run the real checker against the real committed lock
plus small synthetic locks for the boundary cases, and pin the gate's wiring
into the publish workflow (the one place it must never silently vanish from).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.loaders import load_script_module
from tests.paths import REPO_ROOT

gate = load_script_module("scripts/check-lock-age.py", "check_lock_age")

UV_LOCK = REPO_ROOT / "uv.lock"


def test_committed_lock_passes_a_cutoff_after_every_upload() -> None:
    # Not the moving now-7d window (a fresh Dependabot bump would legitimately
    # trip that and this test must not flake with it) — a far-future cutoff
    # proves the pass path over the real lock's hundreds of artifacts.
    violations = gate.find_violations(
        UV_LOCK.read_text(encoding="utf-8"),
        gate.parse_upload_time("2999-01-01T00:00:00Z"),
    )
    assert violations == []


def test_committed_lock_fails_an_ancient_cutoff_naming_packages() -> None:
    violations = gate.find_violations(
        UV_LOCK.read_text(encoding="utf-8"),
        gate.parse_upload_time("2020-01-01T00:00:00Z"),
    )
    assert violations, "every artifact postdates 2020 — the gate must flag them"
    sample = violations[0]
    assert "after the quarantine cutoff" in sample.detail


SYNTHETIC_LOCK = """\
[[package]]
name = "aged"
version = "1.0.0"
sdist = { url = "https://example/aged-1.0.0.tar.gz", upload-time = "2024-01-01T00:00:00Z" }
wheels = [
    { url = "https://example/aged-1.0.0-py3-none-any.whl", upload-time = "2024-01-02T00:00:00Z" },
]

[[package]]
name = "fresh"
version = "2.0.0"
wheels = [
    { url = "https://example/fresh-2.0.0-py3-none-any.whl", upload-time = "2026-07-18T00:00:00Z" },
]

[[package]]
name = "the-project-itself"
version = "0.0.0"
"""


def test_only_artifacts_younger_than_the_cutoff_are_flagged() -> None:
    violations = gate.find_violations(
        SYNTHETIC_LOCK, gate.parse_upload_time("2026-07-12T00:00:00Z")
    )
    assert [(v.package, v.version) for v in violations] == [("fresh", "2.0.0")]


def test_artifact_exactly_at_the_cutoff_is_not_a_violation() -> None:
    # The boundary is strictly-after: an artifact published at the cutoff
    # instant has aged exactly the required window.
    violations = gate.find_violations(
        SYNTHETIC_LOCK, gate.parse_upload_time("2026-07-18T00:00:00Z")
    )
    assert violations == []


def test_artifact_without_upload_time_is_a_violation() -> None:
    # An undatable artifact must not slip the gate silently.
    lock = """\
[[package]]
name = "undated"
version = "1.0.0"
wheels = [{ url = "https://example/undated-1.0.0-py3-none-any.whl" }]
"""
    violations = gate.find_violations(
        lock, gate.parse_upload_time("2999-01-01T00:00:00Z")
    )
    assert [(v.package, v.detail) for v in violations] == [
        ("undated", "artifact has no upload-time recorded")
    ]


def test_cli_matches_the_publish_workflow_invocation(tmp_path: Path) -> None:
    """kit-publish.yml runs the gate through main() with --lock/--max-age-days
    (and the smoke uses --cutoff); pin the CLI contract and the exit codes."""
    lock = tmp_path / "uv.lock"
    lock.write_text(SYNTHETIC_LOCK, encoding="utf-8")
    assert gate.main(["--lock", str(lock), "--cutoff", "2026-07-12T00:00:00Z"]) == 1
    assert gate.main(["--lock", str(lock), "--cutoff", "2026-07-19T00:00:00Z"]) == 0
    # --max-age-days computes the cutoff from now; everything synthetic is old.
    assert gate.main(["--lock", str(lock), "--max-age-days", "0"]) == 0


def test_publish_workflow_runs_the_gate_before_the_build() -> None:
    """The gate is the pipeline's primary supply-chain control: it must be
    invoked by the reusable publish workflow, and before the image build."""
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/kit-publish.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["image"]["steps"]
    gate_index = next(
        (i for i, s in enumerate(steps) if "check-lock-age.py" in s.get("run", "")),
        None,
    )
    build_index = next(
        i for i, s in enumerate(steps) if "build-push-action" in s.get("uses", "")
    )
    assert gate_index is not None, (
        "the publish workflow no longer runs the quarantine gate"
    )
    assert gate_index < build_index, "the gate must run before the image build"
