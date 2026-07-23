"""The echo-agent starter workspace satisfies the launcher contract.

The starter is the template every quickstart user copies (and the add-band
bootstrap scaffolds), so its shape is a shipped contract: a complete locked uv
project, proxy-managed custody by default (plaintext env-file as the gitignored
fallback), and sandbox-owned runtime paths. Pure file checks — no Docker daemon
needed.
"""

from __future__ import annotations

import re

import yaml
from dotenv import dotenv_values

from band.docker.launcher import CredentialName, CredentialSource, WorkspaceConfig
from tests.paths import KIT_DIR

ECHO_AGENT_DIR = KIT_DIR / "echo-agent"


def load_workspace_config() -> WorkspaceConfig:
    raw = yaml.safe_load((ECHO_AGENT_DIR / "band.yaml").read_text(encoding="utf-8"))
    return WorkspaceConfig.model_validate(raw)


def test_starter_ships_a_complete_locked_workspace() -> None:
    config = load_workspace_config()
    assert (ECHO_AGENT_DIR / config.agent.entrypoint).is_file()
    assert (ECHO_AGENT_DIR / "pyproject.toml").is_file()
    assert (ECHO_AGENT_DIR / "uv.lock").is_file(), (
        "the echo-agent starter must ship a committed lock — unlocked resolution is not supported"
    )


def test_starter_defaults_to_proxy_managed_custody() -> None:
    # The starter ships the secure tier by default: no plaintext file, no
    # acknowledgement — the real keys never enter the VM.
    credentials = load_workspace_config().credentials
    assert credentials is not None
    assert credentials.source is CredentialSource.PROXY_MANAGED
    assert credentials.path is None
    assert credentials.acknowledge_plaintext_in_sandbox is False


def test_starter_gitignores_the_fallback_secrets_dir() -> None:
    # Even on the plaintext fallback tier, a copied workspace must never commit
    # secrets, so `.band/` stays gitignored.
    gitignore = (ECHO_AGENT_DIR / ".gitignore").read_text(encoding="utf-8")
    assert ".band/" in gitignore


def test_starter_runtime_paths_are_sandbox_owned() -> None:
    runtime = load_workspace_config().runtime
    # Outside the workspace mount and outside the immutable SDK home.
    for value in (
        runtime.environment_path,
        runtime.state_path,
        runtime.cache_path,
        runtime.log_path,
    ):
        assert value.startswith("/home/agent/"), value


def test_secrets_template_names_match_documented_names() -> None:
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
