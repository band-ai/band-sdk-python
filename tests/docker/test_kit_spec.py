"""The kit spec and echo-agent starter stay coherent with the launcher contract.

Pure file/contract checks — no Docker daemon and no sbx CLI needed, so these
run in the ordinary unit suite. `sbx kit validate` itself is a manual step
(recorded in the kit README) because the sandbox CLI only exists on
Docker-Sandbox-capable machines.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from dotenv import dotenv_values

from band.docker.launcher import CredentialName, WorkspaceConfig

KIT_DIR = Path(__file__).parents[2] / "docker" / "band_python_kit"
ECHO_AGENT_DIR = KIT_DIR / "echo-agent"

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


def test_echo_agent_workspace_satisfies_the_launcher_contract() -> None:
    raw = yaml.safe_load((ECHO_AGENT_DIR / "band.yaml").read_text(encoding="utf-8"))
    config = WorkspaceConfig.model_validate(raw)

    assert (ECHO_AGENT_DIR / config.agent.entrypoint).is_file()
    assert (ECHO_AGENT_DIR / "pyproject.toml").is_file()
    assert (ECHO_AGENT_DIR / "uv.lock").is_file(), (
        "the echo-agent starter must ship a committed lock — unlocked resolution is not supported"
    )

    credentials = config.credentials
    assert credentials is not None
    assert credentials.acknowledge_plaintext_in_sandbox is True
    # The configured credential path must be covered by the echo-agent starter's
    # .gitignore so a copied workspace never commits secrets.
    gitignore = (ECHO_AGENT_DIR / ".gitignore").read_text(encoding="utf-8")
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


def test_echo_agent_secrets_template_names_match_documented_names() -> None:
    """The shipped template must mention every documented credential name and
    nothing else — the launcher rejects undocumented names at launch."""
    template = ECHO_AGENT_DIR / "secrets.env.example"
    active = set(dotenv_values(template))
    commented = set(
        re.findall(
            r"^#\s*([A-Z][A-Z0-9_]*)=",
            template.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    assert "BAND_API_KEY" in active
    assert active | commented == {name.value for name in CredentialName}


def test_dockerfile_wires_the_quarantine_cutoff_into_every_uv_sync() -> None:
    """The release pipeline's supply-chain gate is the UV_EXCLUDE_NEWER build
    arg. uv silently ignores an absent flag, so a refactor that drops the
    wiring from a `uv sync` branch leaves release builds green while the gate
    degrades to decorative — pin the wiring here, where it can fail loudly."""
    dockerfile = (KIT_DIR / "Dockerfile").read_text(encoding="utf-8")

    # The arg must exist and default to empty (local builds resolve untouched).
    assert re.search(r'^ARG UV_EXCLUDE_NEWER=""$', dockerfile, re.MULTILINE)
    # It must feed the flag the release workflow's cutoff rides in on.
    assert "--exclude-newer $UV_EXCLUDE_NEWER" in dockerfile

    # And every dependency sync must carry the flag variable — both the
    # SDK_EXTRA branch and the core-only branch. (Invocations only; comment
    # lines also mention `uv sync`.)
    sync_invocations = [
        line
        for line in dockerfile.splitlines()
        if "uv sync" in line and not line.strip().startswith("#")
    ]
    assert len(sync_invocations) >= 2, (
        "expected both uv sync branches in the Dockerfile"
    )
    for line in sync_invocations:
        assert "$EXCLUDE_NEWER_ARGS" in line, (
            f"sync without the quarantine flag: {line.strip()}"
        )


# ── scripts/stamp-kit-spec.py (release-time image-pin helper) ────────────────

_IMAGE_REF = "ghcr.io/band-ai/band-python-kit/image:1.2.0"
_DIGEST = "sha256:" + "a" * 64


def test_stamp_pins_sandbox_image_by_digest(stamp_kit_spec: ModuleType) -> None:
    stamped = stamp_kit_spec.stamp_spec_file(KIT_DIR / "spec.yaml", _IMAGE_REF, _DIGEST)
    result = yaml.safe_load(stamped)
    assert result["sandbox"]["image"] == f"{_IMAGE_REF}@{_DIGEST}"


def test_stamp_keeps_every_non_image_field_byte_identical(
    stamp_kit_spec: ModuleType,
) -> None:
    original = (KIT_DIR / "spec.yaml").read_text(encoding="utf-8")
    stamped = stamp_kit_spec.stamp_spec_text(original, _IMAGE_REF, _DIGEST)

    orig_lines = original.splitlines()
    stamped_lines = stamped.splitlines()
    assert len(orig_lines) == len(stamped_lines)

    # Exactly one line differs, and it is the sandbox.image line.
    diffs = [(o, s) for o, s in zip(orig_lines, stamped_lines) if o != s]
    assert len(diffs) == 1
    old_line, new_line = diffs[0]
    assert old_line.strip().startswith("image:")
    assert new_line.strip() == f"image: {_IMAGE_REF}@{_DIGEST}"

    # And the parsed spec matches the original in every field except the image.
    orig_spec = yaml.safe_load(original)
    stamped_spec = yaml.safe_load(stamped)
    orig_spec["sandbox"]["image"] = stamped_spec["sandbox"]["image"]
    assert orig_spec == stamped_spec


@pytest.mark.parametrize(
    "bad_digest",
    [
        "deadbeef",  # no algorithm prefix
        "sha256:deadbeef",  # too short
        "sha256:" + "a" * 63,  # off by one
        "sha256:" + "a" * 65,  # off by one the other way
        "sha256:" + "A" * 64,  # uppercase hex
        "sha256:" + "g" * 64,  # non-hex character
        "sha512:" + "a" * 64,  # unsupported algorithm
        "",
    ],
)
def test_stamp_rejects_malformed_digests(
    stamp_kit_spec: ModuleType, bad_digest: str
) -> None:
    with pytest.raises(ValueError):
        stamp_kit_spec.validate_digest(bad_digest)
    with pytest.raises(ValueError):
        stamp_kit_spec.stamp_spec_file(KIT_DIR / "spec.yaml", _IMAGE_REF, bad_digest)


def test_stamp_cli_matches_the_publish_workflow_invocation(
    stamp_kit_spec: ModuleType, tmp_path: Path
) -> None:
    """kit-publish.yml runs the script through main() with these exact flags;
    the library-level tests can't catch a renamed flag or broken --output
    writing, so pin the CLI contract the release actually uses."""
    output = tmp_path / "spec.yaml"
    exit_code = stamp_kit_spec.main(
        [
            "--spec",
            str(KIT_DIR / "spec.yaml"),
            "--image-ref",
            _IMAGE_REF,
            "--digest",
            _DIGEST,
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    assert output.read_text(encoding="utf-8") == stamp_kit_spec.stamp_spec_file(
        KIT_DIR / "spec.yaml", _IMAGE_REF, _DIGEST
    )


def test_stamp_rejects_a_spec_without_a_sandbox_image(
    stamp_kit_spec: ModuleType,
) -> None:
    with pytest.raises(ValueError):
        stamp_kit_spec.stamp_spec_text("kind: sandbox\nname: x\n", _IMAGE_REF, _DIGEST)


def test_stamp_refuses_a_spec_with_ambiguous_image_lines(
    stamp_kit_spec: ModuleType,
) -> None:
    # If the spec ever grows a second `image:` line, the byte-preserving edit
    # must refuse rather than silently rewrite the wrong one — a mis-stamp here
    # could, e.g., corrupt the network allowlist instead of the image ref.
    ambiguous = (
        "kind: sandbox\n"
        "sandbox:\n"
        "  image: band-python-kit:local\n"
        "somethingElse:\n"
        "  image: unrelated:tag\n"
    )
    with pytest.raises(ValueError, match="exactly one"):
        stamp_kit_spec.stamp_spec_text(ambiguous, _IMAGE_REF, _DIGEST)
