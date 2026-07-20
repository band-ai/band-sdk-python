"""Drift checks: the kit spec ships exactly the contract that was reviewed.

Identity, headless launch shape, network allowlist, no credential plumbing.
Pure file/contract checks — no Docker daemon and no sbx CLI needed, so these
run in the ordinary unit suite. `sbx kit validate` itself is a manual step
(recorded in the kit README) because the sandbox CLI only exists on
Docker-Sandbox-capable machines.

The echo-agent starter's contract lives in test_echo_agent.py; the release
stamp helper's tests in test_stamp_spec.py; the supply-chain quarantine
gate's in test_lock_age.py.
"""

from __future__ import annotations

import importlib
from typing import Any

import yaml

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


def test_spec_defines_no_proxy_or_credential_entries() -> None:
    spec = load_spec()
    # Proxy vars are runtime-owned (overriding them bypasses policy), and
    # credential injection belongs to the later proxy-custody milestone.
    env_vars = spec.get("environment", {}).get("variables", {})
    assert not {k for k in env_vars if k.upper().endswith("_PROXY")}
    assert "credentials" not in spec
