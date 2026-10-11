"""Drift checks: the kit spec ships exactly the contract that was reviewed.

Identity, attached-entrypoint launch shape, network allowlist, and credential
injection (Band only; no baked secrets). Pure file/contract checks — no Docker
daemon and no sbx CLI needed, so these
run in the ordinary unit suite. `sbx kit validate` itself is a manual step
(recorded in the kit README) because the sandbox CLI only exists on
Docker-Sandbox-capable machines.

The echo-agent starter's contract lives in test_echo_agent.py; the release
artifact checks use the pinned TCK; the supply-chain quarantine
gate's tests are in test_lock_age.py.
"""

from __future__ import annotations

import importlib
import re
from typing import Any

import yaml

from band.credentials import PROXY_MANAGED_API_KEY
from tests.docker.toolkit.sbx_cli import kit_baseline_hosts
from tests.paths import KIT_DIR

# Hosts required by every fresh starter workspace (see the kit spec's
# comments). Provider, repository, and custom-service hosts are scoped per
# sandbox by the operator rather than baked into the kit.
DEFAULT_ALLOWLIST_HOSTS = {
    "app.band.ai",
    "pypi.org",
    "files.pythonhosted.org",
}


def load_spec() -> dict[str, Any]:
    return yaml.safe_load((KIT_DIR / "spec.yaml").read_text(encoding="utf-8"))


def test_spec_selects_workload_frontend_without_legacy_runtime_fields() -> None:
    spec = load_spec()
    assert spec["schemaVersion"] == "3"
    assert spec["kind"] == "workload"
    assert spec["dockerfile"] == "Dockerfile"
    assert not {"name", "sandbox", "permissions", "agentInstructions"} & spec.keys()
    assert (KIT_DIR / "Dockerfile").is_file()


def test_workload_declares_only_attached_agent_capabilities() -> None:
    entries = load_spec()["capabilities"]
    assert {entry["type"] for entry in entries} == {
        "com.docker.sandbox/sbx@1",
        "com.docker.sandbox/network-policy@1",
        "com.docker.sandbox/agent-context@1",
    }
    context = next(e for e in entries if e["type"].endswith("/agent-context@1"))
    assert "band.yaml" in context["config"]["content"]


def test_launcher_module_is_importable() -> None:
    importlib.import_module("band.docker.launcher")


def test_allowlist_matches_immutable_baseline() -> None:
    assert kit_baseline_hosts(KIT_DIR) == DEFAULT_ALLOWLIST_HOSTS


def test_kit_docs_placeholder_matches_the_sdk_sentinel() -> None:
    """The `sbx secret set-custom --placeholder` value in the shipped kit docs
    must equal the SDK's PROXY_MANAGED_API_KEY — the single origin of the
    sentinel. YAML/shell can't import the constant, so this guard fails CI if a
    doc mirror drifts from it. spec.yaml's comment block carries the same
    command, so it is scanned too."""
    docs = [
        KIT_DIR / "README.md",
        KIT_DIR / "echo-agent" / "README.md",
        KIT_DIR / "spec.yaml",
    ]
    placeholders: set[str] = set()
    for doc in docs:
        placeholders |= set(
            re.findall(r"--placeholder\s+(\S+)", doc.read_text(encoding="utf-8"))
        )
    assert placeholders, "expected at least one --placeholder in the kit docs"
    assert PROXY_MANAGED_API_KEY in placeholders, placeholders
