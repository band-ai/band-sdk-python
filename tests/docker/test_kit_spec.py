"""Drift checks: the kit spec ships exactly the contract that was reviewed.

Identity, headless launch shape, network allowlist, and credential injection
(Band only; no baked secrets). Pure file/contract checks — no Docker daemon and
no sbx CLI needed, so these
run in the ordinary unit suite. `sbx kit validate` itself is a manual step
(recorded in the kit README) because the sandbox CLI only exists on
Docker-Sandbox-capable machines.

The echo-agent starter's contract lives in test_echo_agent.py; the release
stamp helper's tests in test_stamp_spec.py; the supply-chain quarantine
gate's in test_lock_age.py.
"""

from __future__ import annotations

import importlib
import re
from typing import Any

import yaml

from band.credentials import PROXY_MANAGED_API_KEY
from tests.paths import KIT_DIR

# Hosts the launch flow was measured to need (see the kit spec's comments):
# Band, locked dependency sync, and each supported LLM backend.
REQUIRED_ALLOWLIST_HOSTS = {
    "app.band.ai",
    "pypi.org",
    "files.pythonhosted.org",
    "api.openai.com",
    "api.anthropic.com",
    "github.com",
    "api.github.com",
    "release-assets.githubusercontent.com",
    "*.githubcopilot.com",
}

# Copilot plan-variant wildcards: not observed in the smoke (which ran on an
# individual-plan account) but required for business/enterprise accounts.
PLAN_VARIANT_HOSTS = {
    "*.individual.githubcopilot.com",
    "*.business.githubcopilot.com",
    "*.enterprise.githubcopilot.com",
}

# IDE-completion and telemetry hosts that were measured NOT to be needed —
# reintroducing one silently widens the egress surface.
EXCLUDED_HOSTS = {
    "copilot-proxy.githubusercontent.com",
    "origin-tracker.githubusercontent.com",
    "collector.github.com",
    "copilot-telemetry.githubusercontent.com",
    "default.exp-tas.com",
}


def load_spec() -> dict[str, Any]:
    return yaml.safe_load((KIT_DIR / "spec.yaml").read_text(encoding="utf-8"))


def test_spec_is_a_sandbox_kit_with_stable_identity() -> None:
    spec = load_spec()
    # schemaVersion "2" selects sbx's OCI v2 kit artifact format on push. The
    # value is a string in the spec; pin it so a downgrade to legacy ZIP
    # packaging (or an unquoted YAML int) is caught here.
    assert spec["schemaVersion"] == "2"
    assert spec["kind"] == "sandbox"
    # The kit name doubles as the `sbx create` agent positional — renaming it
    # breaks every documented launch command.
    assert spec["name"] == "band-python-kit"
    assert spec["sandbox"]["aiFilename"] == "AGENTS.md"


def test_agent_launches_via_startup_command_not_entrypoint() -> None:
    spec = load_spec()
    # Launch is headless via commands.startup; an entrypoint block would put
    # the agent on the attach path instead (root, PTY, needs a session).
    assert "entrypoint" not in spec["sandbox"]

    startup = spec["commands"]["startup"]
    assert len(startup) == 1
    entry = startup[0]
    assert entry["background"] is True
    assert entry["user"] == "0"
    command = entry["command"]
    # Root phase (CA refresh + privilege drop) must wrap the launcher.
    assert command[0] == "/usr/local/bin/band-entrypoint.sh"
    assert command[-2:] == ["-m", "band.docker.launcher"]


def test_launcher_module_referenced_by_kit_is_importable() -> None:
    spec = load_spec()
    module_name = spec["commands"]["startup"][0]["command"][-1]
    importlib.import_module(module_name)


def test_allowlist_matches_measured_minimal_set() -> None:
    allow = set(load_spec()["caps"]["network"]["allow"])
    # Exact equality: any widening of the egress surface fails this test,
    # not just reintroducing the specifically excluded hosts.
    assert allow == REQUIRED_ALLOWLIST_HOSTS | PLAN_VARIANT_HOSTS
    assert not (EXCLUDED_HOSTS & allow)


def test_spec_bakes_no_credentials_or_proxy_vars() -> None:
    spec = load_spec()
    # Proxy vars are runtime-owned (overriding them bypasses policy).
    env_vars = spec.get("environment", {}).get("variables", {})
    assert not {k for k in env_vars if k.upper().endswith("_PROXY")}
    # Credential injection is provisioned host-side per deployment (via
    # `sbx secret set-custom --host`), so the operator controls the target Band
    # host — the kit bakes no `credentials` block. (A kit-declared
    # `credentials[].apiKey.inject[]` also crashes `sbx create` on 0.35.0.)
    assert "credentials" not in spec


def test_kit_docs_placeholder_matches_the_sdk_sentinel() -> None:
    """The `sbx secret set-custom --placeholder` value in the shipped kit docs
    must equal the SDK's PROXY_MANAGED_API_KEY — the single origin of the
    sentinel. YAML/shell can't import the constant, so this guard fails CI if a
    doc mirror drifts from it."""
    docs = [KIT_DIR / "README.md", KIT_DIR / "echo-agent" / "README.md"]
    placeholders: set[str] = set()
    for doc in docs:
        placeholders |= set(
            re.findall(r"--placeholder\s+(\S+)", doc.read_text(encoding="utf-8"))
        )
    assert placeholders, "expected at least one --placeholder in the kit docs"
    assert placeholders == {PROXY_MANAGED_API_KEY}, placeholders
