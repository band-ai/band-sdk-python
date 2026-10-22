"""Filesystem anchors for the checked-in contract tooling and evidence."""

from __future__ import annotations

from pathlib import Path

CONTRACTS_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = CONTRACTS_ROOT.parent
SPEC_ROOT = CONTRACTS_ROOT / "spec"
ENGINE_ROOT = CONTRACTS_ROOT / "contract-lab"
RUNTIME_ROOT = REPO_ROOT / "src" / "remote_agent"
RUNTIME_TESTS = REPO_ROOT / "tests" / "contracts" / "runtime"
