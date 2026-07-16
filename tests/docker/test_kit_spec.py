"""The kit spec and example workspace stay coherent with the launcher contract.

Pure file/contract checks — no Docker daemon and no sbx CLI needed, so these
run in the ordinary unit suite. `sbx kit validate` itself is a manual step
(recorded in the kit README) because the sandbox CLI only exists on
Docker-Sandbox-capable machines.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import yaml

from band.docker.launcher import WorkspaceConfig

KIT_DIR = Path(__file__).parents[2] / "docker" / "band_python_kit"
EXAMPLE_DIR = KIT_DIR / "example"

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


# Copilot plan-variant wildcards: not observed in the smoke (which ran on an
# individual-plan account) but required for business/enterprise accounts.
PLAN_VARIANT_HOSTS = {
    "*.individual.githubcopilot.com",
    "*.business.githubcopilot.com",
    "*.enterprise.githubcopilot.com",
}


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


def test_example_workspace_satisfies_the_launcher_contract() -> None:
    raw = yaml.safe_load((EXAMPLE_DIR / "band.yaml").read_text(encoding="utf-8"))
    config = WorkspaceConfig.model_validate(raw)

    assert (EXAMPLE_DIR / config.agent.entrypoint).is_file()
    assert (EXAMPLE_DIR / "pyproject.toml").is_file()
    assert (EXAMPLE_DIR / "uv.lock").is_file(), (
        "the example must ship a committed lock — unlocked resolution is not supported"
    )

    credentials = config.credentials
    assert credentials is not None
    assert credentials.acknowledge_plaintext_in_sandbox is True
    # The configured credential path must be covered by the example's
    # .gitignore so a copied workspace never commits secrets.
    gitignore = (EXAMPLE_DIR / ".gitignore").read_text(encoding="utf-8")
    assert credentials.path.split("/")[0] + "/" in gitignore

    # Runtime paths must be sandbox-owned: outside the workspace mount and
    # outside the immutable SDK home.
    for value in (
        config.runtime.environment_path,
        config.runtime.state_path,
        config.runtime.cache_path,
        config.runtime.log_path,
    ):
        assert value.startswith("/home/agent/"), value
