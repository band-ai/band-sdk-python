"""Drift checks: the kit spec ships exactly the contract that was reviewed.

Identity, attached-entrypoint launch shape, network allowlist, and credential
injection (host-held keys; no baked secrets). Pure file/contract checks — no Docker
daemon and no sbx CLI needed, so these
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

DEFAULT_ALLOWLIST_HOSTS = {
    "app.band.ai",
    "pypi.org",
    "files.pythonhosted.org",
    "api.anthropic.com",
}


def load_spec() -> dict[str, Any]:
    return yaml.safe_load((KIT_DIR / "spec.yaml").read_text(encoding="utf-8"))


class TestKitSpec:
    def test_spec_is_a_sandbox_kit_with_stable_identity(self) -> None:
        spec = load_spec()
        # schemaVersion "2" selects sbx's OCI v2 kit artifact format on push. The
        # value is a string in the spec; pin it so a downgrade to legacy ZIP
        # packaging (or an unquoted YAML int) is caught here.
        assert spec["schemaVersion"] == "2"
        assert spec["kind"] == "sandbox"
        # The kit name doubles as the `sbx create` agent positional — renaming it
        # breaks every documented launch command.
        assert spec["name"] == "band-python-kit"
        assert spec["agentInstructions"]["filename"] == "AGENTS.md"

    def test_agent_launches_via_entrypoint_not_interactive_command(self) -> None:
        spec = load_spec()
        # `sandbox.command` is the interactive `sbx run` attach path (a PTY
        # session); this kit's agent runs as the attached foreground process
        # itself via `sandbox.entrypoint`, so `command` must stay unused.
        assert "command" not in spec["sandbox"]

        command = spec["sandbox"]["entrypoint"]
        # Root phase (CA refresh + privilege drop) must wrap the launcher.
        assert command[0] == "/usr/local/bin/band-entrypoint.sh"
        assert command[-2:] == ["-m", "band.docker.launcher"]

    def test_launcher_module_referenced_by_kit_is_importable(self) -> None:
        spec = load_spec()
        module_name = spec["sandbox"]["entrypoint"][-1]
        importlib.import_module(module_name)

    def test_allowlist_matches_immutable_baseline(self) -> None:
        allow = set(load_spec()["permissions"]["network"]["allow"])
        # Catch unreviewed egress expansion as well as missing startup hosts.
        assert allow == DEFAULT_ALLOWLIST_HOSTS

    def test_claude_authentication_targets_only_its_allowed_api_host(self) -> None:
        spec = load_spec()
        (credential,) = spec["credentials"]
        assert credential["service"] == "anthropic"
        assert credential["required"] is False
        api_key = credential["apiKey"]
        assert api_key["name"] == "ANTHROPIC_API_KEY"
        assert api_key["proxyManaged"] is True
        assert api_key["inject"] == [
            {"domain": "api.anthropic.com", "header": "x-api-key", "format": "%s"}
        ]
        assert api_key["inject"][0]["domain"] in spec["permissions"]["network"]["allow"]

    def test_spec_leaves_secrets_and_proxy_configuration_to_the_host(self) -> None:
        spec = load_spec()
        env_vars = spec.get("environment", {}).get("variables", {})
        assert not {key for key in env_vars if key.upper().endswith("_PROXY")}
        assert not {"BAND_API_KEY", "ANTHROPIC_API_KEY"} & env_vars.keys()
        assert "band" not in {
            credential["service"] for credential in spec["credentials"]
        }

    def test_kit_docs_placeholder_matches_the_sdk_sentinel(self) -> None:
        """The `sbx secret set-custom --placeholder` value in the shipped kit docs
        must equal the SDK's PROXY_MANAGED_API_KEY — the single origin of the
        sentinel. YAML/shell can't import the constant, so this guard fails CI if a
        doc mirror drifts from it. Scan the spec too, so any inline setup
        examples obey the same contract."""
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
        assert placeholders == {PROXY_MANAGED_API_KEY}, placeholders
