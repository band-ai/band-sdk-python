"""The echo-agent starter workspace satisfies the launcher contract.

The starter is the template every quickstart user copies (and the add-band
bootstrap scaffolds), so its shape is a shipped contract: a complete locked
uv project, opt-in gitignored credentials, and sandbox-owned runtime paths.
Pure file checks — no Docker daemon needed.
"""

from __future__ import annotations

import re

import yaml
from dotenv import dotenv_values

from band.docker.launcher import CredentialName, WorkspaceConfig
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


def test_starter_credentials_are_opt_in_and_gitignored() -> None:
    credentials = load_workspace_config().credentials
    assert credentials is not None
    assert credentials.acknowledge_plaintext_in_sandbox is True
    # The credential file's directory must be gitignored so a copied
    # workspace never commits secrets.
    credentials_dir = credentials.path.split("/")[0] + "/"
    gitignore = (ECHO_AGENT_DIR / ".gitignore").read_text(encoding="utf-8")
    assert credentials_dir in gitignore


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
